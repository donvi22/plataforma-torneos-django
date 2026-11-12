"""Contract tests for IONOS storage; all network boundaries are mocked."""
import base64
import hashlib
import socket
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from .storage_ionos import IonosAvatarStorage


class IonosConnectionTests(SimpleTestCase):
    def setUp(self):
        self.storage = IonosAvatarStorage()
        self.address = ('::1', 22, 0, 0)
        self.dns = self.enterContext(patch(
            'usuarios.storage_ionos.socket.getaddrinfo',
            return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, '', self.address)],
        ))
        self.socket_factory = self.enterContext(patch('usuarios.storage_ionos.socket.socket'))
        self.sock = self.socket_factory.return_value
        self.transport_factory = self.enterContext(patch('usuarios.storage_ionos.paramiko.Transport'))
        self.transport = self.transport_factory.return_value
        self.sftp_factory = self.enterContext(patch('usuarios.storage_ionos.paramiko.SFTPClient.from_transport'))
        self.sftp = self.sftp_factory.return_value
        self.enterContext(patch.dict('os.environ', {
            'IONOS_SFTP_USERNAME': 'test-only-user',
            'IONOS_SFTP_PASSWORD': 'test-only-password',
        }))
        key = self.transport.get_remote_server_key.return_value
        key.get_name.return_value = 'ssh-ed25519'
        key.asbytes.return_value = b'synthetic-test-host-key'
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        self.enterContext(patch.object(self.storage, 'FINGERPRINT', fingerprint))
        self.transport.is_authenticated.return_value = True

    def test_connection_checks_identity_authenticates_and_closes(self):
        with self.storage._connection() as sftp:
            self.assertIs(sftp, self.sftp)
            self.sftp.close.assert_not_called()
        self.dns.assert_called_once_with(self.storage.HOST, 22, socket.AF_INET6, socket.SOCK_STREAM)
        self.sock.settimeout.assert_called_once_with(15)
        self.sock.connect.assert_called_once_with(self.address)
        self.transport.start_client.assert_called_once_with(timeout=15)
        self.transport.auth_password.assert_called_once_with('test-only-user', 'test-only-password')
        self.sftp_factory.assert_called_once_with(self.transport)
        self.sftp.close.assert_called_once_with()
        self.transport.close.assert_called_once_with()

    def test_wrong_fingerprint_rejected_before_authentication(self):
        self.storage.FINGERPRINT = 'incorrect-test-fingerprint'
        with self.assertRaisesRegex(RuntimeError, 'identidad'):
            with self.storage._connection():
                self.fail('Untrusted server must not be exposed')
        self.transport.auth_password.assert_not_called()
        self.sftp_factory.assert_not_called()
        self.transport.close.assert_called_once_with()

    def test_wrong_key_type_rejected_before_authentication(self):
        self.transport.get_remote_server_key.return_value.get_name.return_value = 'ssh-rsa'
        with self.assertRaisesRegex(RuntimeError, 'identidad'):
            with self.storage._connection():
                self.fail('Untrusted key must not be exposed')
        self.transport.auth_password.assert_not_called()
        self.transport.close.assert_called_once_with()

    def test_unsuccessful_authentication_closes_transport(self):
        self.transport.is_authenticated.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Autenticacion'):
            with self.storage._connection():
                self.fail('Unauthenticated connection must not be exposed')
        self.sftp_factory.assert_not_called()
        self.transport.close.assert_called_once_with()

    def test_authentication_exception_is_propagated_and_transport_closed(self):
        self.transport.auth_password.side_effect = OSError('auth unavailable')
        with self.assertRaisesRegex(OSError, 'auth unavailable'):
            with self.storage._connection():
                pass
        self.transport.close.assert_called_once_with()
        self.sftp_factory.assert_not_called()

    def test_socket_failure_closes_socket_before_transport_exists(self):
        self.sock.connect.side_effect = TimeoutError('connect timeout')
        with self.assertRaisesRegex(TimeoutError, 'connect timeout'):
            with self.storage._connection():
                pass
        self.sock.close.assert_called_once_with()
        self.transport_factory.assert_not_called()

    def test_body_exception_still_closes_resources(self):
        with self.assertRaisesRegex(OSError, 'operation failed'):
            with self.storage._connection():
                raise OSError('operation failed')
        self.sftp.close.assert_called_once_with()
        self.transport.close.assert_called_once_with()


class IonosStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = IonosAvatarStorage()
        self.connection = self.enterContext(patch.object(self.storage, '_connection'))
        self.sftp = self.connection.return_value.__enter__.return_value
        # Defense in depth: accidentally bypassing the connection mock fails
        # before DNS resolution, even on a machine with real credentials.
        self.enterContext(patch('usuarios.storage_ionos.socket.getaddrinfo', side_effect=AssertionError('Real network forbidden')))
        self.enterContext(patch('usuarios.storage_ionos.socket.socket', side_effect=AssertionError('Real socket forbidden')))

    def test_remote_names_strip_upload_prefix(self):
        self.assertEqual(self.storage._remote_path('avatars/photo.png'), 'photo.png')
        self.assertEqual(self.storage._remote_path('photo.png'), 'photo.png')

    def test_unsafe_paths_are_rejected(self):
        for name in ('/photo.png', '../photo.png', 'avatars/../photo.png', 'avatars/nested/photo.png', '', '.', 'avatars/'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.storage._remote_path(name)
        self.connection.assert_not_called()

    def test_url_uses_public_host_and_quotes_filename_without_network(self):
        self.assertEqual(self.storage.url('avatars/foto prueba.png'), 'https://media.rankedkingdom.com/avatars/foto%20prueba.png')
        self.connection.assert_not_called()

    def prepare_upload(self):
        self.sftp.stat.side_effect = [SimpleNamespace(st_size=5), FileNotFoundError()]
        return ContentFile(b'hello')

    def test_upload_publishes_verified_temporary_file(self):
        content = self.prepare_upload()
        self.assertEqual(self.storage._save('avatars/photo.png', content), 'avatars/photo.png')
        temporary, mode = self.sftp.open.call_args.args
        self.assertRegex(temporary, r'^\.upload_[0-9a-f]{32}\.tmp$')
        self.assertEqual(mode, 'wx')
        destination = self.sftp.open.return_value.__enter__.return_value
        destination.write.assert_called_once_with(b'hello')
        self.assertEqual([call.args[0] for call in self.sftp.stat.call_args_list], [temporary, 'photo.png'])
        self.sftp.rename.assert_called_once_with(temporary, 'photo.png')
        self.sftp.remove.assert_not_called()

    def test_incomplete_upload_is_not_published_and_is_cleaned(self):
        self.sftp.stat.return_value.st_size = 4
        with self.assertRaisesRegex(OSError, 'incompleta'):
            self.storage._save('avatars/photo.png', ContentFile(b'hello'))
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])
        self.sftp.rename.assert_not_called()

    def test_existing_destination_is_not_overwritten(self):
        self.sftp.stat.side_effect = [SimpleNamespace(st_size=5), SimpleNamespace(st_size=10)]
        with self.assertRaises(FileExistsError):
            self.storage._save('avatars/photo.png', ContentFile(b'hello'))
        self.sftp.rename.assert_not_called()
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])

    def test_failed_rename_cleans_temporary_and_preserves_error(self):
        content = self.prepare_upload()
        self.sftp.rename.side_effect = OSError('rename failed')
        with self.assertRaisesRegex(OSError, 'rename failed'):
            self.storage._save('avatars/photo.png', content)
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])

    def test_cleanup_failure_is_logged_without_hiding_original_error(self):
        self.sftp.open.side_effect = OSError('write failed')
        self.sftp.remove.side_effect = PermissionError('cleanup denied')
        with self.assertLogs('usuarios.storage_ionos', level='ERROR') as logs:
            with self.assertRaisesRegex(OSError, 'write failed'):
                self.storage._save('avatars/photo.png', ContentFile(b'hello'))
        self.assertIn('No se pudo limpiar', logs.output[0])
        self.sftp.rename.assert_not_called()

    def test_read_returns_detached_file_and_rejects_write_mode(self):
        self.sftp.open.return_value.__enter__.return_value.read.return_value = b'image bytes'
        with self.storage._open('avatars/photo.png') as content:
            self.assertEqual(content.read(), b'image bytes')
            self.assertEqual(content.name, 'avatars/photo.png')
        self.sftp.open.assert_called_once_with('photo.png', 'rb')
        self.connection.reset_mock()
        with self.assertRaises(ValueError):
            self.storage._open('avatars/photo.png', 'wb')
        self.connection.assert_not_called()

    def test_exists_distinguishes_missing_file_from_service_failure(self):
        self.sftp.stat.side_effect = [SimpleNamespace(st_size=5), FileNotFoundError(), PermissionError('denied')]
        self.assertTrue(self.storage.exists('avatars/photo.png'))
        self.assertFalse(self.storage.exists('avatars/photo.png'))
        with self.assertRaises(PermissionError):
            self.storage.exists('avatars/photo.png')

    def test_delete_is_idempotent_but_preserves_service_errors(self):
        self.storage.delete('')
        self.connection.assert_not_called()
        self.storage.delete('avatars/photo.png')
        self.sftp.remove.assert_called_once_with('photo.png')
        self.sftp.remove.side_effect = FileNotFoundError()
        self.storage.delete('avatars/photo.png')
        self.sftp.remove.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            self.storage.delete('avatars/photo.png')

    def test_size_uses_remote_metadata(self):
        self.sftp.stat.return_value.st_size = 123
        self.assertEqual(self.storage.size('avatars/photo.png'), 123)
        self.sftp.stat.assert_called_once_with('photo.png')
