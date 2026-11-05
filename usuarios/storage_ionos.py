
import base64
import hashlib
import io
import os
import socket
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath

import paramiko
from django.core.files.base import File
from django.core.files.storage import Storage


class IonosAvatarStorage(Storage):
    HOST = "access-5021649008.webspace-host.com"
    FINGERPRINT = "1gx2w8Rtv3wCgi7Jh8myf/KVd72cRQbow03UP8P095Q"
    BASE_URL = "https://media.rankedkingdom.com/avatars/"

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
                os.environ["IONOS_SFTP_USERNAME"],
                os.environ["IONOS_SFTP_PASSWORD"],
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

        with self._connection() as sftp:
            # Crear sin sobrescribir un archivo existente.
            with sftp.open(remote, "xb") as destination:
                for chunk in content.chunks():
                    destination.write(chunk)

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
