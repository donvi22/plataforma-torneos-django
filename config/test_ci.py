"""Regressions for CI safety guards; never connect to an external endpoint."""
import socket
import os
import runpy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .ci_runner import REQUIRED_CONCURRENCY, StrictPostgresRunner, assert_concurrency_coverage, loopback_database_only


class CIGuardTests(SimpleTestCase):
    def suite(self):
        suite = unittest.TestSuite()
        for name, count in REQUIRED_CONCURRENCY.items():
            module, _, cls_name = name.rpartition('.')
            cls = type(cls_name, (unittest.TestCase,), {'__module__': module, 'test_case': lambda self: None})
            suite.addTests(cls('test_case') for _ in range(count))
        return suite

    def test_required_concurrency_classes_are_present(self):
        self.assertEqual(assert_concurrency_coverage(self.suite()), REQUIRED_CONCURRENCY)

    def test_missing_concurrency_suite_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            assert_concurrency_coverage(unittest.TestSuite())

    def test_skipped_concurrency_case_fails(self):
        suite = self.suite()
        test = next(iter(suite))
        type(test).__unittest_skip__ = True
        with self.assertRaises(ImproperlyConfigured):
            assert_concurrency_coverage(suite)

    def test_external_connect_and_connect_ex_are_blocked(self):
        with loopback_database_only(5432), socket.socket() as sock:
            for connect in (sock.connect, sock.connect_ex):
                with self.assertRaises(AssertionError):
                    connect(('example.invalid', 22))
                with self.assertRaises(AssertionError):
                    connect(('127.0.0.1', 22))

    def test_external_dns_is_blocked_and_guard_restores_socket(self):
        original = socket.getaddrinfo
        with loopback_database_only(5432):
            with self.assertRaises(AssertionError):
                socket.getaddrinfo('example.invalid', 22)
        self.assertIs(socket.getaddrinfo, original)

    def test_only_configured_loopback_endpoint_is_allowed(self):
        with patch.object(socket.socket, 'connect') as connect:
            with loopback_database_only(5432), socket.socket() as sock:
                sock.connect(('127.0.0.1', 5432))
            connect.assert_called_once_with(sock, ('127.0.0.1', 5432))

    def test_skipped_tests_make_ci_fail(self):
        result = SimpleNamespace(testsRun=1, failures=[], errors=[], unexpectedSuccesses=[], skipped=[('case', 'reason')])
        self.assertEqual(StrictPostgresRunner(verbosity=0).suite_result(None, result), 1)

    def test_successful_result_is_success(self):
        result = SimpleNamespace(testsRun=1, failures=[], errors=[], unexpectedSuccesses=[], skipped=[])
        self.assertEqual(StrictPostgresRunner(verbosity=0).suite_result(None, result), 0)

    def test_ci_settings_refuse_ambient_service_credentials(self):
        for key in ('PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'DATABASE_URL',
                    'IONOS_SFTP_PASSWORD', 'IONOS_MEDIA_SFTP_PASSWORD', 'RAILWAY_PROJECT_ID'):
            with self.subTest(key=key), patch.dict(os.environ, {key:'synthetic'}, clear=True):
                with self.assertRaises(ImproperlyConfigured):
                    runpy.run_module('config.ci_settings')

    def test_ci_settings_cannot_be_selected_for_production(self):
        with patch.dict(os.environ, {'DJANGO_DEBUG':'False'}, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                runpy.run_module('config.ci_settings')

    def test_failed_checks_are_not_converted_into_success(self):
        result = SimpleNamespace(testsRun=2, failures=[('case','failure')], errors=[('case','error')], unexpectedSuccesses=[], skipped=[])
        self.assertEqual(StrictPostgresRunner(verbosity=0).suite_result(None, result), 2)
