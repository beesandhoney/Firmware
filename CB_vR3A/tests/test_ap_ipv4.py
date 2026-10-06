import types
import unittest
from unittest.mock import Mock
from test_http_recovery import load_method


class APIPv4Test(unittest.TestCase):
    def setUp(self):
        self.configure = load_method('captive_portal.py', 'CaptivePortal',
                                     '_configure_ap_ipv4', {})
        self.ap = Mock()
        self.portal = types.SimpleNamespace(ap_if=self.ap, AP_IP='192.168.4.1')

    def test_default_address_does_not_restart_dhcp(self):
        # DNS may be unset in ESP-IDF's defaults; DNS is disabled in the
        # current diagnostic firmware, and must not cause an IPv4 rewrite.
        self.ap.ifconfig.return_value = ('192.168.4.1', '255.255.255.0', '192.168.4.1', '0.0.0.0')
        self.configure(self.portal)
        self.assertTrue(all(not call.args for call in self.ap.ifconfig.call_args_list))
        self.assertEqual(self.portal.local_ip, '192.168.4.1')

    def test_changed_subnet_is_configured_once_and_verified(self):
        target = ('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1')
        self.ap.ifconfig.side_effect = [
            ('192.168.10.1', '255.255.255.0', '192.168.10.1', '0.0.0.0'), None, target]
        self.configure(self.portal)
        writes = [call.args for call in self.ap.ifconfig.call_args_list if call.args]
        self.assertEqual(writes, [(target,)])

    def test_failed_configuration_is_not_reported_ready(self):
        self.ap.ifconfig.return_value = ('0.0.0.0', '0.0.0.0', '0.0.0.0', '0.0.0.0')
        with self.assertRaises(RuntimeError):
            self.configure(self.portal)
