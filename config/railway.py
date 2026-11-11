import argparse
import os

import django
from django.conf import settings
from django.core.management import call_command


def main(argv=None):
    parser = argparse.ArgumentParser(description='Preparacion Railway: solo configuracion PostgreSQL de produccion.')
    parser.add_argument('stage', choices=('build', 'predeploy'))
    stage = parser.parse_args(argv).stage
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    if settings.DEBUG or settings.DEMO_MODE or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
        raise SystemExit('Railway requiere DJANGO_DEBUG=False, PostgreSQL completo y TORNEO_DEMO desactivado.')
    django.setup()
    if stage == 'build':
        call_command('collectstatic', interactive=False)
    else:
        call_command('migrate', interactive=False)


if __name__ == '__main__':
    main()