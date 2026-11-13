"""CI guards: PostgreSQL coverage, no skipped tests and no Python external I/O."""
import multiprocessing
import socket
from contextlib import contextmanager
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases

REQUIRED_CONCURRENCY = {
    'usuarios.test_xp_integrity.XPPostgreSQLConcurrencyTests': 3,
    'partidas.test_result_integrity.ResultPostgreSQLTests': 7,
    'arbitraje.test_integrity.ArbitrationConcurrencyTests': 20,
}


def assert_concurrency_coverage(suite):
    counts = dict.fromkeys(REQUIRED_CONCURRENCY, 0)
    for test in iter_test_cases(suite):
        cls = type(test)
        name = f'{cls.__module__}.{cls.__name__}'
        if name in counts:
            if getattr(cls, '__unittest_skip__', False) or getattr(
                getattr(test, test._testMethodName), '__unittest_skip__', False,
            ):
                raise ImproperlyConfigured(f'Concurrency test marked skipped: {test.id()}')
            counts[name] += 1
    for name, minimum in REQUIRED_CONCURRENCY.items():
        if counts[name] < minimum:
            raise ImproperlyConfigured(f'Missing concurrency coverage: {name} ({counts[name]} < {minimum})')
    return counts


@contextmanager
def loopback_database_only(port):
    """Not an OS firewall: restrict Python socket calls; DB config fixes libpq."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def allowed(address):
        return isinstance(address, tuple) and address[:2] == ('127.0.0.1', port)

    def connect(sock, address):
        if not allowed(address):
            raise AssertionError('External network connections are forbidden during CI tests.')
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not allowed(address):
            raise AssertionError('External network connections are forbidden during CI tests.')
        return original_connect_ex(sock, address)

    def resolve(host, service, *args, **kwargs):
        if host != '127.0.0.1' or str(service) != str(port):
            raise AssertionError('External DNS/network resolution is forbidden during CI tests.')
        return original_resolve(host, service, *args, **kwargs)

    with patch.object(socket.socket, 'connect', connect), patch.object(socket.socket, 'connect_ex', connect_ex), patch.object(socket, 'getaddrinfo', resolve):
        yield


class StrictPostgresRunner(DiscoverRunner):
    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        for name, count in assert_concurrency_coverage(suite).items():
            self.log(f'CI concurrency coverage: {name}: {count}')
        return suite

    def run_tests(self, *args, **kwargs):
        db = settings.DATABASES['default']
        if (connection.vendor != 'postgresql' or db['HOST'] != '127.0.0.1'
                or db['NAME'] != 'rankedkingdom_ci' or db['TEST']['NAME'] != 'test_rankedkingdom_ci'
                or 'fork' not in multiprocessing.get_all_start_methods()):
            raise ImproperlyConfigured('CI requires the isolated PostgreSQL configuration and Linux fork.')
        with loopback_database_only(int(db['PORT'])):
            return super().run_tests(*args, **kwargs)

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        self.log(f'CI result: total={result.testsRun}, failures={len(result.failures)}, '
                 f'errors={len(result.errors)}, skipped={len(result.skipped)}')
        # Do not silently turn PostgreSQL coverage into a green skipped run.
        return failures + len(result.skipped)
