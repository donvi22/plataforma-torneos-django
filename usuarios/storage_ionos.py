import base64
import hashlib
import io
import logging
import os
import re
import socket
import stat
import warnings
from contextlib import contextmanager
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit
from uuid import uuid4

import paramiko
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class IonosAvatarStorage(Storage):
    HOST = "access-5021649008.webspace-host.com"
    FINGERPRINT = "1gx2w8Rtv3wCgi7Jh8myf/KVd72cRQbow03UP8P095Q"
    BASE_URL = "https://media.rankedkingdom.com/avatars/"
    USERNAME_ENV = "IONOS_SFTP_USERNAME"
    PASSWORD_ENV = "IONOS_SFTP_PASSWORD"

    def _remote_path(self, name):
        path = PurePosixPath(name)

        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Ruta de avatar no permitida")

        parts = path.parts

        if parts and parts[0] == "avatars":
            parts = parts[1:]

        if len(parts) != 1 or parts[0] in ("", "."):
            raise ValueError("Nombre de avatar no permitido")

        return parts[0]

    @contextmanager
    def _connection(self):
        sock = None
        transport = None
        sftp = None

        try:
            address = socket.getaddrinfo(
                self.HOST,
                22,
                socket.AF_INET6,
                socket.SOCK_STREAM,
            )[0][4]

            sock = socket.socket(
                socket.AF_INET6,
                socket.SOCK_STREAM,
            )
            sock.settimeout(15)
            sock.connect(address)

            transport = paramiko.Transport(sock)
            transport.start_client(timeout=15)

            key = transport.get_remote_server_key()
            fingerprint = base64.b64encode(
                hashlib.sha256(key.asbytes()).digest()
            ).decode().rstrip("=")

            if (
                key.get_name() != "ssh-ed25519"
                or fingerprint != self.FINGERPRINT
            ):
                raise RuntimeError(
                    "La identidad del servidor IONOS no coincide"
                )

            transport.auth_password(
                os.environ[self.USERNAME_ENV],
                os.environ[self.PASSWORD_ENV],
            )

            if not transport.is_authenticated():
                raise RuntimeError("Autenticacion SFTP fallida")

            sftp = paramiko.SFTPClient.from_transport(transport)
            yield sftp

        finally:
            if sftp is not None:
                sftp.close()
            if transport is not None:
                transport.close()
            elif sock is not None:
                sock.close()

    
    def _save(self, name, content):
        remote = self._remote_path(name)
        temporal = f".upload_{uuid4().hex}.tmp"
        logger = logging.getLogger(__name__)

        with self._connection() as sftp:
            try:
                total_bytes = 0

                # Subir utilizando un nombre temporal único.
                with sftp.open(temporal, "wx") as destination:
                    for chunk in content.chunks():
                        destination.write(chunk)
                        total_bytes += len(chunk)

                # Comprobar que llegó el archivo completo.
                remote_size = sftp.stat(temporal).st_size

                if remote_size != total_bytes:
                    raise OSError(
                        "La subida del avatar esta incompleta"
                    )

                # Evitar sobrescribir archivos existentes.
                try:
                    sftp.stat(remote)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError(
                        "El avatar de destino ya existe"
                    )

                # Publicar únicamente después de la subida.
                sftp.rename(temporal, remote)

            except Exception:
                # Intentar limpiar una subida incompleta.
                try:
                    sftp.remove(temporal)
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.exception(
                        "No se pudo limpiar el temporal SFTP"
                    )
                raise

        return name


    def _open(self, name, mode="rb"):
        if mode not in ("rb", "r"):
            raise ValueError("Modo de apertura no permitido")

        with self._connection() as sftp:
            with sftp.open(self._remote_path(name), "rb") as source:
                data = source.read()

        return File(io.BytesIO(data), name=name)

    def exists(self, name):
        with self._connection() as sftp:
            try:
                sftp.stat(self._remote_path(name))
                return True
            except FileNotFoundError:
                return False

    def delete(self, name):
        if not name:
            return

        with self._connection() as sftp:
            try:
                sftp.remove(self._remote_path(name))
            except FileNotFoundError:
                pass

    def size(self, name):
        with self._connection() as sftp:
            return sftp.stat(self._remote_path(name)).st_size

    def url(self, name):
        from urllib.parse import quote
        return self.BASE_URL + quote(self._remote_path(name))


# The legacy avatar storage intentionally keeps its existing remote paths.
# Other media use a separate, explicitly configured root and versioned names.
MEDIA_PREFIX = 'ionos_v1'
MEDIA_CATEGORIES = frozenset(('videojuegos', 'torneos', 'premios'))
REMOTE_NAME = re.compile(r'ionos_v1/(videojuegos|torneos|premios)/[0-9a-f]{32}\.(jpg|png|gif|webp)\Z')
MAX_MEDIA_BYTES = 5 * 1024 * 1024
MAX_MEDIA_PIXELS = 16_000_000


def _media_name(name):
    """Validate raw paths before normalization can hide traversal segments."""
    if not isinstance(name, str) or not name or any(c in name for c in '\\%?#:'):
        raise SuspiciousFileOperation('Ruta multimedia no permitida.')
    parts = name.split('/')
    if any(p in ('', '.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in parts):
        raise SuspiciousFileOperation('Ruta multimedia no permitida.')
    if parts[0] == MEDIA_PREFIX:
        if not REMOTE_NAME.fullmatch(name):
            raise SuspiciousFileOperation('Identificador multimedia persistente no valido.')
    elif parts[0] not in MEDIA_CATEGORIES or len(parts) < 2:
        raise SuspiciousFileOperation('Categoria multimedia no permitida.')
    return parts


@deconstructible
class IonosMediaStorage(IonosAvatarStorage):
    """New media only: configured absolute root, never the avatar SFTP cwd."""

    USERNAME_ENV = "IONOS_MEDIA_SFTP_USERNAME"
    PASSWORD_ENV = "IONOS_MEDIA_SFTP_PASSWORD"

    @contextmanager
    def _connection(self):
        # Never fall back to the avatar account or contact the host without
        # both dedicated credentials. Do not include secret values in errors.
        if any(not os.environ.get(name, '').strip() for name in (self.USERNAME_ENV, self.PASSWORD_ENV)):
            raise OSError('Faltan credenciales SFTP multimedia dedicadas.')
        try:
            with super()._connection() as sftp:
                yield sftp
        except (paramiko.SSHException, RuntimeError, KeyError) as error:
            raise OSError('No se pudo establecer una conexion multimedia SFTP verificada.') from error

    def configuration(self):
        root = getattr(settings, 'IONOS_MEDIA_REMOTE_ROOT', '')
        base = getattr(settings, 'IONOS_MEDIA_PUBLIC_BASE_URL', '')
        if not root or not root.startswith('/') or root == '/' or '\\' in root:
            raise ImproperlyConfigured('IONOS_MEDIA_REMOTE_ROOT requiere un directorio absoluto dedicado y verificado.')
        if any(p in ('', '.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in root[1:].split('/')):
            raise ImproperlyConfigured('IONOS_MEDIA_REMOTE_ROOT no es una ruta absoluta canonica.')
        try:
            parsed = urlsplit(base)
        except ValueError as error:
            raise ImproperlyConfigured('IONOS_MEDIA_PUBLIC_BASE_URL no es una URL valida.') from error
        if (not base or base.strip() != base or any(ord(c) < 32 or ord(c) == 127 for c in base) or parsed.scheme != 'https' or parsed.netloc != 'media.rankedkingdom.com'
                or parsed.query or parsed.fragment or not parsed.path.endswith('/')
                or parsed.path in ('/', '/avatars/') or '%' in parsed.path or '\\' in parsed.path
                or any(p in ('', '.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in parsed.path.split('/')[1:-1])):
            raise ImproperlyConfigured('IONOS_MEDIA_PUBLIC_BASE_URL requiere una carpeta HTTPS dedicada en media.rankedkingdom.com.')
        return root, base

    def _remote_path(self, name):
        _media_name(name)
        if not REMOTE_NAME.fullmatch(name):
            raise SuspiciousFileOperation('Este almacenamiento solo admite identificadores persistentes.')
        root, _ = self.configuration()
        return root + '/' + name.removeprefix(MEDIA_PREFIX + '/')

    def _checked_parent(self, sftp, remote):
        # Require pre-provisioned real directories. Never mkdir in an unknown
        # hosting tree, follow symlinks, or write relative to the login cwd.
        parent = PurePosixPath(remote).parent
        current = PurePosixPath('/')
        for component in parent.parts[1:]:
            current /= component
            if not stat.S_ISDIR(sftp.lstat(str(current)).st_mode):
                raise OSError('El directorio multimedia falta o no es un directorio real.')

    def get_available_name(self, name, max_length=None):
        self._remote_path(name)
        if max_length is not None and len(name) > max_length:
            raise SuspiciousFileOperation('El identificador multimedia supera max_length.')
        # A collision must fail, never fall back to an overwrite or a new path
        # outside the validated identifier grammar.
        return name

    def _save(self, name, content):
        remote = self._remote_path(name)
        temporary = str(PurePosixPath(remote).parent / f'.upload_{uuid4().hex}.tmp')
        with self._connection() as sftp:
            self._checked_parent(sftp, remote)
            created = False
            try:
                # Paramiko's standard rename has no-overwrite semantics.
                # Do not replace this with posix_rename (which overwrites).
                try:
                    sftp.lstat(remote)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError('El archivo multimedia de destino ya existe.')
                with sftp.open(temporary, 'wx') as destination:
                    created = True
                    size = 0
                    for chunk in content.chunks():
                        size += len(chunk)
                        if size > MAX_MEDIA_BYTES:
                            raise OSError('El archivo multimedia supera el limite de subida.')
                        destination.write(chunk)
                metadata = sftp.lstat(temporary)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
                    raise OSError('La subida multimedia esta incompleta.')
                sftp.rename(temporary, remote)
            except Exception:
                if created:
                    try:
                        sftp.remove(temporary)
                    except FileNotFoundError:
                        pass
                    except Exception:
                        logging.getLogger(__name__).exception('No se pudo limpiar el temporal multimedia SFTP')
                raise
        return name

    def _metadata(self, sftp, remote):
        self._checked_parent(sftp, remote)
        metadata = sftp.lstat(remote)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError('El archivo multimedia no es un archivo regular.')
        return metadata

    def _open(self, name, mode='rb'):
        remote = self._remote_path(name)
        if mode not in ('rb', 'r'):
            raise ValueError('Modo de apertura no permitido.')
        with self._connection() as sftp:
            self._metadata(sftp, remote)
            with sftp.open(remote, 'rb') as source:
                data = source.read(MAX_MEDIA_BYTES + 1)
                if len(data) > MAX_MEDIA_BYTES:
                    raise OSError('El archivo multimedia supera el limite de lectura.')
        return File(io.BytesIO(data), name=name)

    def exists(self, name):
        remote = self._remote_path(name)
        with self._connection() as sftp:
            try:
                self._metadata(sftp, remote)
                return True
            except FileNotFoundError:
                return False

    def size(self, name):
        remote = self._remote_path(name)
        with self._connection() as sftp:
            return self._metadata(sftp, remote).st_size

    def delete(self, name):
        if not name:
            return
        remote = self._remote_path(name)
        with self._connection() as sftp:
            try:
                self._metadata(sftp, remote)
                sftp.remove(remote)
            except FileNotFoundError:
                pass

    def url(self, name):
        self._remote_path(name)
        _, base = self.configuration()
        return base + quote(name.removeprefix(MEDIA_PREFIX + '/'), safe='/')


@deconstructible
class PersistentMediaStorage(Storage):
    """Keep legacy references local; only new production uploads get remote IDs."""

    def __init__(self):
        self.local = FileSystemStorage()
        self.remote = IonosMediaStorage()

    def _backend(self, name):
        parts = _media_name(name)
        return self.remote if parts[0] == MEDIA_PREFIX else self.local

    def save(self, name, content, max_length=None):
        name = name if name is not None else getattr(content, 'name', None)
        parts = _media_name(name)
        if len(parts) != 2 or parts[0] not in MEDIA_CATEGORIES:
            raise SuspiciousFileOperation('Una subida nueva necesita categoria y nombre de archivo.')
        mode = getattr(settings, 'MEDIA_STORAGE_MODE', 'local' if settings.DEBUG else 'ionos')
        if mode == 'local':
            return self.local.save(name, content, max_length=max_length)
        if mode != 'ionos':
            raise ImproperlyConfigured('Produccion requiere almacenamiento multimedia IONOS.')
        self.remote.configuration()  # Fail closed; never fall back to local disk.
        content.seek(0)
        data = content.read(MAX_MEDIA_BYTES + 1)
        if len(data) > MAX_MEDIA_BYTES:
            raise ValidationError('La imagen no puede superar los 5 MB.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    extension = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}.get(image.format)
                    if not extension or image.width * image.height > MAX_MEDIA_PIXELS:
                        raise ValueError('Formato o dimensiones no permitidos.')
                    image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValidationError('La imagen debe ser PNG, JPEG, GIF o WebP valida, de hasta 16 megapixeles.') from error
        identifier = f'{MEDIA_PREFIX}/{parts[0]}/{uuid4().hex}.{extension}'
        return self.remote.save(identifier, ContentFile(data), max_length=max_length)

    def _open(self, name, mode='rb'):
        return self._backend(name).open(name, mode)

    def exists(self, name):
        return self._backend(name).exists(name)

    def size(self, name):
        return self._backend(name).size(name)

    def delete(self, name):
        if name:
            self._backend(name).delete(name)

    def url(self, name):
        return self._backend(name).url(name)

    def path(self, name):
        return self._backend(name).path(name)


@register(Tags.security, deploy=True)
def check_persistent_media_configuration(app_configs, **kwargs):
    """Offline deployment gate; does not assert that remote mapping is verified."""
    if getattr(settings, 'MEDIA_STORAGE_MODE', 'local') != 'ionos':
        return []
    try:
        IonosMediaStorage().configuration()
    except ImproperlyConfigured as error:
        return [Error(str(error), hint='Ver docs/almacenamiento-multimedia.md; verificar el mapeo fisico antes del despliegue.', id='usuarios.E001')]
    return []
