import importlib.util
from pathlib import Path
import select
import socket
import sys
import unittest
from unittest.mock import patch, Mock


class CaptiveDNSTest(unittest.TestCase):
    def setUp(self):
        with patch.dict(sys.modules, {'usocket': socket, 'uselect': select}):
            spec = importlib.util.spec_from_file_location('dns_under_test',
                Path(__file__).resolve().parents[1] / 'captive_dns.py')
            self.dns = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.dns)

    def query(self, qtype=1):
        return (b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
                b'\x07captive\x05apple\x03com\x00' + bytes((0, qtype, 0, 1)))

    def test_ipv4_captive_resolution(self):
        answer = self.dns.DNSQuery(self.query()).answer('192.168.4.1')
        self.assertEqual(answer[:2], b'\x12\x34')
        self.assertEqual(answer[6:8], b'\x00\x01')
        self.assertEqual(answer[-4:], bytes((192, 168, 4, 1)))

    def test_ipv6_empty_answer_and_edns_not_copied(self):
        query = self.query(28)
        answer = self.dns.DNSQuery(query + b'additional data').answer('192.168.4.1')
        self.assertEqual(answer[6:12], b'\x00' * 6)
        self.assertEqual(answer[12:], query[12:])

    def test_malformed_packets_rejected(self):
        for packet in (b'', self.query()[:-1], self.query()[:12] + b'\xc0\x0c'):
            with self.assertRaises(ValueError):
                self.dns.DNSQuery(packet)

    def test_idle_and_bad_datagrams_do_not_block_service(self):
        server = self.dns.DNSServer.__new__(self.dns.DNSServer)
        server.sock = Mock()
        server.ip_addr = '192.168.4.1'
        server.sock.recvfrom.side_effect = OSError(11)
        server.serve_once()
        server.sock.recvfrom.side_effect = None
        server.sock.recvfrom.return_value = (b'bad', ('192.168.4.2', 1234))
        server.serve_once()
        server.sock.sendto.assert_not_called()
        server.sock.recvfrom.return_value = (self.query(), ('192.168.4.2', 1234))
        server.serve_once()
        self.assertEqual(server.sock.sendto.call_args[0][0][-4:], bytes((192, 168, 4, 1)))
