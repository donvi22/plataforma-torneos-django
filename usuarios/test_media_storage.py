"""Persistent media routing and SFTP operations without a real host or DB."""
import io
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, override_settings

from .storage_ionos import IonosAvatarStorage, IonosMediaStorage, PersistentMediaStorage, MAX_MEDIA_BYTES

NAME = 'ionos_v1/torneos/' + 'a' * 32 + '.png'
ROOT = '/verified-media-root'
BASE = 'https://media.rankedkingdom.com/uploads/'
REMOTE = ROOT + '/torneos/' + 'a' * 32 + '.png'
SETTINGS = dict(MEDIA_STORAGE_MODE='ionos', IONOS_MEDIA_REMOTE_ROOT=ROOT, IONOS_MEDIA_PUBLIC_BASE_URL=BASE)


def image_file():
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, 'PNG')
    return ContentFile(buffer.getvalue(), name='photo.png')


@override_settings(**SETTINGS)
class IonosMediaStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = IonosMediaStorage()
        self.connection = self.enterContext(patch.object(self.storage, '_connection'))
        self.sftp = self.connection.return_value.__enter__.return_value
        self.enterContext(patch('usuarios.storage_ionos.socket.getaddrinfo', side_effect=AssertionError('Real DNS forbidden')))
        self.enterContext(patch('usuarios.storage_ionos.socket.socket', side_effect=AssertionError('Real SFTP forbidden')))
        self.sftp.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

    def upload_metadata(self, size):
        # /verified-media-root, category, destination, uploaded temporary.
        self.sftp.lstat.side_effect = [
            SimpleNamespace(st_mode=stat.S_IFDIR), SimpleNamespace(st_mode=stat.S_IFDIR),
            FileNotFoundError(), SimpleNamespace(st_mode=stat.S_IFREG, st_size=size),
        ]

    def test_success_writes_under_absolute_root_not_login_directory(self):
        self.upload_metadata(5)
        self.assertEqual(self.storage.save(NAME, ContentFile(b'hello')), NAME)
        temporary, mode = self.sftp.open.call_args.args
        self.assertTrue(temporary.startswith(ROOT + '/torneos/.upload_'))
        self.assertEqual(mode, 'wx')
        self.sftp.open.return_value.__enter__.return_value.write.assert_called_once_with(b'hello')
        self.sftp.rename.assert_called_once_with(temporary, REMOTE)
        self.sftp.remove.assert_not_called()

    def test_duplicate_destination_is_never_overwritten_or_deleted(self):
        self.sftp.lstat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR)] * 2 + [SimpleNamespace(st_mode=stat.S_IFREG)]
        with self.assertRaises(FileExistsError):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.open.assert_not_called()
        self.sftp.rename.assert_not_called()
        self.sftp.remove.assert_not_called()

    def test_partial_upload_is_cleaned_without_publishing(self):
        self.upload_metadata(4)
        with self.assertRaisesRegex(OSError, 'incompleta'):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])
        self.sftp.rename.assert_not_called()

    def test_failed_write_cleans_only_our_temporary(self):
        self.upload_metadata(5)
        self.sftp.open.return_value.__enter__.return_value.write.side_effect = OSError('write failed')
        with self.assertRaisesRegex(OSError, 'write failed'):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])
        self.sftp.rename.assert_not_called()

    def test_rename_race_fails_without_overwrite_or_destination_cleanup(self):
        self.upload_metadata(5)
        self.sftp.rename.side_effect = FileExistsError('another upload won')
        with self.assertRaises(FileExistsError):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.remove.assert_called_once_with(self.sftp.open.call_args.args[0])
        self.sftp.posix_rename.assert_not_called()

    def test_cleanup_failure_is_logged_and_original_failure_propagates(self):
        self.upload_metadata(4)
        self.sftp.remove.side_effect = PermissionError('cleanup denied')
        with self.assertLogs('usuarios.storage_ionos', level='ERROR'):
            with self.assertRaisesRegex(OSError, 'incompleta'):
                self.storage.save(NAME, ContentFile(b'hello'))

    def test_connection_failure_does_not_attempt_file_operations(self):
        self.connection.return_value.__enter__.side_effect = TimeoutError('offline')
        with self.assertRaises(TimeoutError):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.open.assert_not_called()
        self.sftp.remove.assert_not_called()

    def test_symlinked_or_missing_parent_cannot_receive_upload(self):
        for mode in (stat.S_IFLNK, stat.S_IFREG):
            with self.subTest(mode=mode):
                self.sftp.lstat.return_value = SimpleNamespace(st_mode=mode)
                with self.assertRaises(OSError):
                    self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.lstat.side_effect = FileNotFoundError()
        with self.assertRaises(FileNotFoundError):
            self.storage.save(NAME, ContentFile(b'hello'))
        self.sftp.open.assert_not_called()
        self.sftp.mkdir.assert_not_called()

    def test_nested_root_checks_every_parent(self):
        with override_settings(IONOS_MEDIA_REMOTE_ROOT='/hosting/site/uploads'):
            self.sftp.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR)
            self.storage._checked_parent(self.sftp, '/hosting/site/uploads/torneos/file.png')
        self.assertEqual([c.args[0] for c in self.sftp.lstat.call_args_list], ['/hosting', '/hosting/site', '/hosting/site/uploads', '/hosting/site/uploads/torneos'])

    def test_invalid_paths_fail_before_connection(self):
        for name in ('../photo.png', '/photo.png', 'ionos_v1/../photo.png', NAME.replace('/torneos/', '//torneos/'), NAME.replace('/torneos/', '/torneos/./'), NAME.replace('torneos', 'avatars'), NAME.replace('.png', '.html'), NAME.replace('a' * 32, 'a%2fb'), NAME.replace('torneos/', 'torneos\\'), NAME + '\x00', 'torneos/legacy.png'):
            with self.subTest(name=name), self.assertRaises((SuspiciousFileOperation, ValueError)):
                self.storage.save(name, ContentFile(b'x'))
        self.connection.assert_not_called()

    def test_bad_configuration_fails_before_connection(self):
        for root in ('', '/', 'relative', '/a/../b', '/a//b', '/a/', '/a\\b'):
            with self.subTest(root=root), override_settings(IONOS_MEDIA_REMOTE_ROOT=root), self.assertRaises(ImproperlyConfigured):
                self.storage.save(NAME, ContentFile(b'x'))
        for base in ('https://[', ' ' + BASE, BASE + '\n', '', 'http://media.rankedkingdom.com/uploads/', 'https://evil.example/uploads/', 'https://media.rankedkingdom.com/', 'https://media.rankedkingdom.com/avatars/', BASE+'?secret=x', BASE+'#x', 'https://media.rankedkingdom.com/a/../uploads/'):
            with self.subTest(base=base), override_settings(IONOS_MEDIA_PUBLIC_BASE_URL=base), self.assertRaises(ImproperlyConfigured):
                self.storage.url(NAME)
        self.connection.assert_not_called()

    def test_public_url_and_remote_path_have_same_category_and_filename(self):
        self.assertEqual(self.storage._remote_path(NAME), REMOTE)
        self.assertEqual(self.storage.url(NAME), BASE + 'torneos/' + 'a' * 32 + '.png')
        self.connection.assert_not_called()

    def test_max_length_is_checked_before_connection(self):
        with self.assertRaises(SuspiciousFileOperation):
            self.storage.save(NAME, ContentFile(b'x'), max_length=10)
        self.connection.assert_not_called()

    def metadata_for_read(self):
        self.sftp.lstat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR)] * 2 + [SimpleNamespace(st_mode=stat.S_IFREG, st_size=5)]

    def test_read_is_detached_and_bounded(self):
        self.metadata_for_read()
        self.sftp.open.return_value.__enter__.return_value.read.return_value = b'hello'
        with self.storage.open(NAME) as content:
            self.assertEqual(content.read(), b'hello')
        self.sftp.open.assert_called_once_with(REMOTE, 'rb')
        self.sftp.open.return_value.__enter__.return_value.read.assert_called_once_with(MAX_MEDIA_BYTES + 1)

    def test_exists_size_and_delete_use_exact_remote_target(self):
        self.metadata_for_read()
        self.assertTrue(self.storage.exists(NAME))
        self.metadata_for_read()
        self.assertEqual(self.storage.size(NAME), 5)
        self.metadata_for_read()
        self.storage.delete(NAME)
        self.sftp.remove.assert_called_once_with(REMOTE)

    def test_missing_delete_is_idempotent_but_permission_errors_propagate(self):
        self.sftp.lstat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR)] * 2 + [FileNotFoundError()]
        self.storage.delete(NAME)
        self.sftp.remove.assert_not_called()
        self.sftp.lstat.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            self.storage.exists(NAME)

    def test_symlink_file_is_never_read_or_deleted(self):
        for method in (self.storage.open, self.storage.delete, self.storage.size):
            self.sftp.lstat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR)] * 2 + [SimpleNamespace(st_mode=stat.S_IFLNK)]
            with self.assertRaises(OSError):
                method(NAME)
        self.sftp.open.assert_not_called()
        self.sftp.remove.assert_not_called()


@override_settings(**SETTINGS)
class PersistentMediaRoutingTests(SimpleTestCase):
    def setUp(self):
        self.storage = PersistentMediaStorage()
        self.remote = self.enterContext(patch.object(self.storage.remote, '_connection'))
        self.enterContext(patch('usuarios.storage_ionos.socket.getaddrinfo', side_effect=AssertionError('Real DNS forbidden')))
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.directory.name, MEDIA_URL='/media/'))

    def test_all_dynamic_non_avatar_fields_use_router(self):
        from videojuegos.models import Videojuego
        from torneos.models import PremioTorneo, Torneo
        for model, field in ((Videojuego, 'imagen'), (Torneo, 'imagen_banner'), (PremioTorneo, 'imagen')):
            self.assertIs(model._meta.get_field(field).storage, default_storage)
            self.assertIsInstance(default_storage._wrapped, PersistentMediaStorage)

    def test_each_category_gets_a_new_persistent_identifier_and_actual_upload(self):
        sftp = self.remote.return_value.__enter__.return_value
        for category in ('videojuegos', 'torneos', 'premios'):
            content = image_file()
            sftp.lstat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR)] * 2 + [FileNotFoundError(), SimpleNamespace(st_mode=stat.S_IFREG, st_size=content.size)]
            name = self.storage.save(category + '/untrusted.html', content)
            self.assertRegex(name, rf'^ionos_v1/{category}/[0-9a-f]{{32}}\.png$')
            self.assertTrue(sftp.rename.call_args.args[1].startswith(ROOT + '/' + category + '/'))
            self.assertEqual(sftp.open.return_value.__enter__.return_value.write.call_args.args[0], content.file.getvalue())
            self.assertEqual(self.storage.url(name), BASE + name.removeprefix('ionos_v1/'))

    def test_repeated_original_name_creates_distinct_files(self):
        with patch.object(self.storage.remote, 'save', side_effect=lambda name, content, **kwargs: name):
            first = self.storage.save('torneos/banner.png', image_file())
            second = self.storage.save('torneos/banner.png', image_file())
        self.assertNotEqual(first, second)

    def test_legacy_names_keep_local_urls_bytes_and_deletion(self):
        name = self.storage.local.save('torneos/legacy.png', image_file())
        self.assertEqual(self.storage.url(name), '/media/torneos/legacy.png')
        self.assertTrue(self.storage.exists(name))
        with self.storage.open(name) as content:
            self.assertGreater(len(content.read()), 0)
        self.storage.delete(name)
        self.assertFalse(self.storage.exists(name))
        self.remote.assert_not_called()

    def test_local_development_stays_local(self):
        with override_settings(MEDIA_STORAGE_MODE='local'):
            name = self.storage.save('videojuegos/local.png', image_file())
        self.assertEqual(name, 'videojuegos/local.png')
        self.assertTrue(self.storage.local.exists(name))
        self.remote.assert_not_called()

    def test_production_missing_root_never_falls_back_to_local(self):
        with override_settings(IONOS_MEDIA_REMOTE_ROOT=''), self.assertRaises(ImproperlyConfigured):
            self.storage.save('torneos/banner.png', image_file())
        self.assertFalse(self.storage.local.exists('torneos/banner.png'))
        self.remote.assert_not_called()

    def test_invalid_content_and_oversize_files_fail_before_sftp(self):
        for content in (ContentFile(b'<html>unsafe</html>'), ContentFile(b'x' * (MAX_MEDIA_BYTES + 1))):
            with self.subTest(size=content.size), self.assertRaises(ValidationError):
                self.storage.save('premios/fake.png', content)
        self.remote.assert_not_called()

    def test_unsafe_names_and_unknown_categories_never_connect(self):
        for name in ('../file.png', 'avatars/file.png', 'other/file.png', 'torneos/../file.png', 'torneos/a\\file.png', 'torneos/%2e%2e.png'):
            with self.subTest(name=name), self.assertRaises((SuspiciousFileOperation, ValueError)):
                self.storage.save(name, image_file())
        self.remote.assert_not_called()

    def test_avatar_paths_and_urls_are_unchanged(self):
        avatar = IonosAvatarStorage()
        self.assertEqual(avatar._remote_path('avatars/existing.png'), 'existing.png')
        self.assertEqual(avatar.url('avatars/existing.png'), 'https://media.rankedkingdom.com/avatars/existing.png')
        self.remote.assert_not_called()

    def test_model_field_save_persists_returned_identifier(self):
        from videojuegos.models import Videojuego
        juego = Videojuego(nombre='Unsaved test game')
        with patch.object(default_storage, 'remote', self.storage.remote), patch.object(self.storage.remote, 'save', side_effect=lambda name, content, **kwargs: name) as save:
            juego.imagen.save('photo.png', image_file(), save=False)
        self.assertTrue(juego.imagen.name.startswith('ionos_v1/videojuegos/'))
        self.assertEqual(juego.imagen.name, save.call_args.args[0])

    def test_persistent_names_have_no_local_path(self):
        with self.assertRaises(NotImplementedError):
            self.storage.path(NAME)
        self.remote.assert_not_called()

    def test_new_identifier_respects_model_max_length_before_sftp(self):
        with self.assertRaises(SuspiciousFileOperation):
            self.storage.save('premios/image.png', image_file(), max_length=10)
        self.remote.assert_not_called()

    def test_replacement_does_not_implicitly_delete_previous_remote_file(self):
        with patch.object(self.storage.remote, 'save', side_effect=lambda name, content, **kw: name), patch.object(self.storage.remote, 'delete') as delete:
            old = self.storage.save('torneos/old.png', image_file())
            new = self.storage.save('torneos/new.png', image_file())
        self.assertNotEqual(old, new)
        delete.assert_not_called()

    def test_deployment_check_is_offline_and_rejects_missing_mapping(self):
        from .storage_ionos import check_persistent_media_configuration
        self.assertEqual(check_persistent_media_configuration(None), [])
        with override_settings(IONOS_MEDIA_REMOTE_ROOT=''):
            self.assertEqual(check_persistent_media_configuration(None)[0].id, 'usuarios.E001')
        self.remote.assert_not_called()

    @patch.dict('os.environ', {'IONOS_MEDIA_SFTP_USERNAME': 'media-test', 'IONOS_MEDIA_SFTP_PASSWORD': 'media-test-password'})
    def test_ssh_identity_and_authentication_failures_are_not_silenced(self):
        # The shared transport remains fingerprint-pinned by the avatar tests.
        for error in (RuntimeError('untrusted key'), KeyError('missing test credential')):
            with self.subTest(error=type(error)), patch.object(IonosAvatarStorage, '_connection', side_effect=error):
                with self.assertRaises(OSError) as caught:
                    with IonosMediaStorage()._connection():
                        self.fail('Unverified connection must not be exposed')
                self.assertIs(caught.exception.__cause__, error)
