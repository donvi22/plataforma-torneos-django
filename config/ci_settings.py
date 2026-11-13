"""Disposable CI only: fixed loopback database, no production credentials."""
import os
import tempfile

from django.core.exceptions import ImproperlyConfigured

# Refuse ambient service credentials rather than importing real configuration.
_FORBIDDEN = {'PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'DATABASE_URL'}
if any(value for key, value in os.environ.items()
       if key in _FORBIDDEN or key.startswith(('IONOS_', 'RAILWAY_'))):
    raise ImproperlyConfigured('CI must not receive production/service credentials or PG* configuration.')
if os.environ.get('DJANGO_DEBUG', 'True').lower() not in ('true', '1', 'yes', 'on'):
    raise ImproperlyConfigured('CI requires DJANGO_DEBUG=True; it is not a production settings module.')

from .settings import *  # noqa: F403, E402

DEBUG = True
DEMO_MODE = False
MEDIA_STORAGE_MODE = 'local'
_DATABASE_PORT = int(os.environ.get('CI_POSTGRES_PORT', '5432'))
if not 1 <= _DATABASE_PORT <= 65535:
    raise ImproperlyConfigured('CI_POSTGRES_PORT must be a valid loopback port.')
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'HOST': '127.0.0.1', 'PORT': _DATABASE_PORT,
    'NAME': 'rankedkingdom_ci', 'USER': 'rankedkingdom_ci',
    'PASSWORD': 'ci-only-not-a-secret',
    'CONN_MAX_AGE': 0,
    'OPTIONS': {'sslmode': 'disable'},
    'TEST': {'NAME': 'test_rankedkingdom_ci'},
}}
_MEDIA_DIRECTORY = tempfile.TemporaryDirectory(prefix='rankedkingdom-ci-media-')
MEDIA_ROOT = _MEDIA_DIRECTORY.name
MAILERS = {'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'}}
TEST_RUNNER = 'config.ci_runner.StrictPostgresRunner'
# PASSWORD_HASHERS deliberately inherited unchanged from Django/project defaults.
