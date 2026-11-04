import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config import railway

SETTINGS_FILE = Path(__file__).with_name('settings.py')
VALIDATION_KEY = 'validation-only-not-a-real-secret-' + '0123456789abcdef' * 3


class EnvironmentSettingsTests(SimpleTestCase):
    def load_settings(self, **environment):
        with patch.dict(os.environ, environment, clear=True):
            return runpy.run_path(str(SETTINGS_FILE))

    def production_environment(self, **extra):
        environment = {
            'DJANGO_DEBUG': 'False',
            'DJANGO_SECRET_KEY': VALIDATION_KEY,
            'DJANGO_ALLOWED_HOSTS': ' rankedkingdom.com, www.rankedkingdom.com ',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://rankedkingdom.com, https://www.rankedkingdom.com',
            'PGHOST': '127.0.0.1',
            'PGDATABASE': 'validation_only',
            'PGUSER': 'validation_only',
            'PGPASSWORD': 'validation-only-not-a-real-password',
        }
        environment.update(extra)
        return environment

    def test_local_and_demo_keep_their_sqlite_databases(self):
        for environment, name in (({}, 'db.sqlite3'), ({'TORNEO_DEMO': '1'}, 'db_demo.sqlite3')):
            with self.subTest(name=name):
                settings = self.load_settings(**environment)
                self.assertTrue(settings['DEBUG'])
                self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.sqlite3')
                self.assertEqual(settings['DATABASES']['default']['NAME'].name, name)
                self.assertEqual(settings['ALLOWED_HOSTS'], [])
                self.assertFalse(settings['SESSION_COOKIE_SECURE'])
                self.assertFalse(settings['CSRF_COOKIE_SECURE'])
                self.assertFalse(settings['SECURE_SSL_REDIRECT'])
                self.assertIsNone(settings['SECURE_PROXY_SSL_HEADER'])
                self.assertEqual(settings['STORAGES']['staticfiles']['BACKEND'], 'django.contrib.staticfiles.storage.StaticFilesStorage')
                self.assertEqual(settings['MAILERS']['default']['BACKEND'], 'django.core.mail.backends.console.EmailBackend')

    def test_debug_accepts_explicit_boolean_values(self):
        for value in ('true', '1', ' TRUE ', 'on'):
            with self.subTest(value=value):
                self.assertTrue(self.load_settings(DJANGO_DEBUG=value)['DEBUG'])
        for value in ('false', '0', ' FALSE ', 'off'):
            with self.subTest(value=value):
                settings = self.load_settings(**self.production_environment(DJANGO_DEBUG=value))
                self.assertFalse(settings['DEBUG'])
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(DJANGO_DEBUG='perhaps')

    def test_production_rejects_missing_or_unsafe_secret(self):
        for key in ('', 'short', 'x' * 64, 'django-insecure-' + VALIDATION_KEY):
            with self.subTest(key_length=len(key)), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**self.production_environment(DJANGO_SECRET_KEY=key))

    def test_hosts_and_origins_are_trimmed_and_explicit(self):
        settings = self.load_settings(**self.production_environment())
        self.assertEqual(settings['ALLOWED_HOSTS'], ['rankedkingdom.com', 'www.rankedkingdom.com'])
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'], ['https://rankedkingdom.com', 'https://www.rankedkingdom.com'])
        for hosts in ('', '*', '*.example.com'):
            with self.subTest(hosts=hosts), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**self.production_environment(DJANGO_ALLOWED_HOSTS=hosts))

    def test_postgresql_and_security_configuration(self):
        settings = self.load_settings(**self.production_environment())
        database = settings['DATABASES']['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(database['NAME'], 'validation_only')
        self.assertEqual(database['PORT'], 5432)
        self.assertEqual(database['CONN_MAX_AGE'], 60)
        self.assertTrue(database['CONN_HEALTH_CHECKS'])
        self.assertEqual(database['OPTIONS']['sslmode'], 'require')
        self.assertTrue(settings['SESSION_COOKIE_SECURE'])
        self.assertTrue(settings['CSRF_COOKIE_SECURE'])
        self.assertTrue(settings['SECURE_SSL_REDIRECT'])
        self.assertEqual(settings['SECURE_PROXY_SSL_HEADER'], ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertEqual(settings['SECURE_HSTS_SECONDS'], 0)
        self.assertEqual(settings['STATIC_URL'], 'static/')
        self.assertEqual(settings['STORAGES']['staticfiles']['BACKEND'], 'whitenoise.storage.CompressedManifestStaticFilesStorage')
        self.assertEqual(settings['STORAGES']['default']['BACKEND'], 'django.core.files.storage.FileSystemStorage')
        self.assertEqual(settings['MIDDLEWARE'][1], 'whitenoise.middleware.WhiteNoiseMiddleware')

    def test_production_mail_uses_smtp_without_sending(self):
        settings = self.load_settings(**self.production_environment(
            DJANGO_EMAIL_HOST='smtp.example.invalid', DJANGO_EMAIL_PORT='587',
            DJANGO_EMAIL_HOST_USER='validation_only', DJANGO_EMAIL_HOST_PASSWORD='validation-only',
            DJANGO_DEFAULT_FROM_EMAIL='validation@example.invalid',
        ))
        mailer = settings['MAILERS']['default']
        self.assertEqual(mailer['BACKEND'], 'django.core.mail.backends.smtp.EmailBackend')
        self.assertEqual(mailer['OPTIONS']['host'], 'smtp.example.invalid')
        self.assertEqual(mailer['OPTIONS']['port'], 587)
        self.assertTrue(mailer['OPTIONS']['use_tls'])
        self.assertEqual(settings['DEFAULT_FROM_EMAIL'], 'validation@example.invalid')

    def test_incomplete_database_or_demo_postgres_is_rejected(self):
        for missing in ('PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD'):
            environment = self.production_environment(**{missing: ''})
            with self.subTest(missing=missing), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**environment)
        for port in ('not-a-port', '0', '65536'):
            with self.subTest(port=port), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**self.production_environment(PGPORT=port))
        for environment in (
            self.production_environment(PGHOST='', PGDATABASE='', PGUSER='', PGPASSWORD=''),
            self.production_environment(TORNEO_DEMO='1'),
            {'TORNEO_DEMO': '1', 'PGHOST': '127.0.0.1'},
        ):
            with self.subTest(environment_keys=list(environment)), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**environment)

    def test_railway_stages_are_separate_and_require_production(self):
        production = SimpleNamespace(DEBUG=False, DEMO_MODE=False, DATABASES={'default': {'ENGINE': 'django.db.backends.postgresql'}})
        with patch.object(railway, 'settings', production), patch.object(railway.django, 'setup'), patch.object(railway, 'call_command') as command:
            railway.main(['build'])
            command.assert_called_once_with('collectstatic', interactive=False)
            command.reset_mock()
            railway.main(['predeploy'])
            command.assert_called_once_with('migrate', interactive=False)
        for local in (
            SimpleNamespace(DEBUG=True, DEMO_MODE=False, DATABASES=production.DATABASES),
            SimpleNamespace(DEBUG=False, DEMO_MODE=True, DATABASES=production.DATABASES),
            SimpleNamespace(DEBUG=False, DEMO_MODE=False, DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3'}}),
        ):
            with self.subTest(settings=local), patch.object(railway, 'settings', local), patch.object(railway, 'call_command') as command:
                with self.assertRaises(SystemExit):
                    railway.main(['predeploy'])
                command.assert_not_called()