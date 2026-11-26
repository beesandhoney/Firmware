"""Exercise the device NTP retry policy without hardware or network access."""
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class NTPRetryTest(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'app_main.py').read_text())
        names = {'NTP_HOSTS', 'NTP_FALLBACK_IPS', 'NTP_TIMEOUT_S',
                 'NTP_RETRY_INTERVAL_S', 'ntp_dns_failed', 'ntp_attempt_index', 'next_ntp_retry_ms'}
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)) or
                 (isinstance(n, ast.FunctionDef) and n.name in ('sync_time_once', 'sync_time_before_http'))]
        self.client = types.SimpleNamespace(settime=Mock())
        self.ns = dict(ntptime=self.client, gc=types.SimpleNamespace(collect=lambda: None),
                       utime=types.SimpleNamespace(ticks_ms=lambda: 0, ticks_diff=lambda a,b: a-b, ticks_add=lambda a,b: a+b),
                       WATER_DEBUG=False, portal=types.SimpleNamespace(sta_if=Mock()))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app_main.py', 'exec'), self.ns)
        self.sync = self.ns['sync_time_once']

    def test_dns_failure_uses_numeric_fallback_and_success_clears_failure(self):
        self.client.settime.side_effect = [OSError(-202), None]
        self.assertFalse(self.sync(0))
        self.assertEqual(self.client.host, 'pool.ntp.org')
        self.assertTrue(self.ns['ntp_dns_failed'])
        self.ns['portal'].sta_if.ifconfig.assert_called_once()
        self.assertTrue(self.sync(1))
        self.assertIn(self.client.host, self.ns['NTP_FALLBACK_IPS'])
        self.assertEqual(self.client.timeout, 2)
        self.assertFalse(self.ns['ntp_dns_failed'])

    def test_dns_outage_rotates_ips_and_periodically_retries_dns(self):
        self.client.settime.side_effect = OSError(-202)
        self.sync(0)
        self.client.settime.side_effect = OSError(110)
        hosts = []
        for attempt in (1, 2, 3, 4):
            self.assertFalse(self.sync(attempt))
            hosts.append(self.client.host)
        self.assertNotEqual(hosts[0], hosts[1])
        self.assertIn(hosts[3], self.ns['NTP_HOSTS'])
        self.assertTrue(self.ns['ntp_dns_failed'])

    def test_udp_timeout_keeps_normal_host_rotation(self):
        self.client.settime.side_effect = OSError(110)
        for attempt, host in enumerate(self.ns['NTP_HOSTS']):
            self.assertFalse(self.sync(attempt))
            self.assertEqual(self.client.host, host)
            self.assertFalse(self.ns['ntp_dns_failed'])

    def test_allocation_failure_uses_fallback(self):
        for error in (OSError(12), MemoryError()):
            self.client.settime.side_effect = [error, None]
            self.assertFalse(self.sync(0))
            self.assertTrue(self.ns['ntp_dns_failed'])
            self.assertTrue(self.sync(1))
            self.assertIn(self.client.host, self.ns['NTP_FALLBACK_IPS'])

    def test_startup_fallback_shares_runtime_backoff(self):
        self.ns.update(load_runtime_modules=Mock(), service_startup_controls=Mock())
        self.client.settime.side_effect = [OSError(12), OSError(110)]
        self.ns['sync_time_before_http']()
        self.assertFalse(self.ns['time_synced'])
        self.assertEqual(self.ns['ntp_attempt_index'], 2)
        self.assertEqual(self.ns['next_ntp_retry_ms'], 30000)
        self.assertIn(self.client.host, self.ns['NTP_FALLBACK_IPS'])
        self.ns['service_startup_controls'].assert_called_once()

    def test_successful_startup_does_not_make_second_request(self):
        self.ns.update(load_runtime_modules=Mock(), service_startup_controls=Mock())
        self.ns['sync_time_before_http']()
        self.assertTrue(self.ns['time_synced'])
        self.client.settime.assert_called_once()

    def test_invalid_ntp_packet_does_not_claim_dns_failure(self):
        self.client.settime.side_effect = OSError(-1)
        self.assertFalse(self.sync(0))
        self.assertFalse(self.ns['ntp_dns_failed'])

    def test_keyboard_interrupt_is_not_swallowed(self):
        self.client.settime.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.sync(0)
