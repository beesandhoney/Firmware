"""Exercise the external workload against real local HTTP sockets, not the ESP32."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socketserver
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import portal_memory_workload as workload


class WorkloadHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        try:
            if self.path == '/invalid-http':
                self.connection.sendall(b'not an HTTP status line\r\n\r\n')
                self.close_connection = True
                return
            if self.path == '/delayed':
                time.sleep(0.25)
            if self.path == '/redirect':
                self.send_response(307)
                self.send_header('Location', '/')
                self.send_header('Content-Length', '0')
                self.send_header('Connection', 'close')
                self.end_headers()
                return
            if self.path == '/huge':
                self.send_response(200)
                self.send_header('Content-Length', str(workload.MAX_BODY_BYTES + 1))
                self.send_header('Connection', 'close')
                self.end_headers()
                return
            kind = self.server.path_kinds.get(self.path, 'html')
            values = {
                'status': {key: {} for key in ('clock', 'lighting', 'water', 'nutrients', 'temperature')},
                'portal': dict(connected=False, connecting=False, ssid='PRIVATE-SAVED-NETWORK'),
                'settings': dict(timezone='Europe/Stockholm', lighting={}),
                'networks': dict(networks=['PRIVATE-SCAN-NETWORK']),
            }
            body = b'<html><body>Garden</body></html>' if kind == 'html' else json.dumps(values.get(kind, {})).encode()
            content_type = 'text/html' if kind == 'html' else 'application/json'
            status = 200
            if kind == 'empty':
                body, status = b'', 404
            if self.path == '/invalid-json':
                body, content_type = b'{broken', 'application/json'
            if self.path == '/wrong-json':
                body, content_type = b'[]', 'application/json'
            if self.path == '/trickle':
                body = b'xxxxxxxxxxxxxxxxxxxx'
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            if self.path != '/missing-length':
                self.send_header('Content-Length', str(len(body) + (10 if self.path == '/short' else 0)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.path == '/trickle':
                for value in body:
                    self.wfile.write(bytes((value,)))
                    self.wfile.flush()
                    time.sleep(0.03)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # Avoid HTTPServer's reverse-DNS query in this numeric localhost test.
        socketserver.TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


class PortalMemoryWorkloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalHTTPServer(('127.0.0.1', 0), WorkloadHandler)
        cls.server.daemon_threads = True
        cls.server.path_kinds = {step.path: step.kind for stage in workload.STAGES for step in stage}
        cls.server.calls = []
        cls.address = cls.server.server_address
        cls.url = 'http://127.0.0.1:%s/' % cls.address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=1)

    def request(self, path, kind='html', status=200, timeout=0.5):
        return workload.request_once(self.address, workload.Step('test', path, kind, status), timeout)

    def test_complete_html_has_matching_length_and_digest(self):
        result = self.request('/')
        self.assertTrue(result['ok'])
        self.assertEqual(result['bytes_received'], result['content_length'])
        self.assertEqual(len(result['body_sha256']), 64)

    def test_truncated_response_fails_completeness(self):
        result = self.request('/short')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'incomplete_body')
        self.assertEqual(result['content_length'] - result['bytes_received'], 10)

    def test_invalid_json_and_wrong_shape_are_distinguished(self):
        self.assertEqual(self.request('/invalid-json', 'settings')['error'], 'invalid_json')
        self.assertEqual(self.request('/wrong-json', 'settings')['error'], 'invalid_json_shape')

    def test_redirect_is_reported_without_following_it(self):
        before = len(self.server.calls)
        result = self.request('/redirect')
        self.assertEqual(result['status'], 307)
        self.assertEqual(result['error'], 'unexpected_status')
        self.assertEqual(self.server.calls[before:], ['/redirect'])

    def test_missing_length_and_oversize_body_fail(self):
        self.assertEqual(self.request('/missing-length')['error'], 'missing_or_invalid_content_length')
        self.assertEqual(self.request('/huge')['error'], 'body_too_large')

    def test_invalid_http_and_refused_connections_are_reported(self):
        self.assertEqual(self.request('/invalid-http')['error'], 'invalid_http')
        # Some host firewalls silently drop an unused-port connection instead
        # of refusing it. Inject the OS exception to check its classification.
        connection = Mock()
        connection.connect.side_effect = ConnectionRefusedError()
        with patch.object(workload.http.client, 'HTTPConnection', return_value=connection):
            result = self.request('/')
        self.assertEqual(result['error'], 'connection_refused')

    def test_header_timeout_and_trickle_body_have_total_deadlines(self):
        for path in ('/delayed', '/trickle'):
            with self.subTest(path=path):
                started = time.monotonic()
                result = self.request(path, timeout=0.12)
                self.assertEqual(result['error'], 'timeout')
                self.assertLess(time.monotonic() - started, 0.6)

    def test_network_and_saved_ssids_never_appear_in_records(self):
        for path, kind in (('/api/networks', 'networks'), ('/api/portal', 'portal')):
            result = self.request(path, kind)
            self.assertTrue(result['ok'])
            encoded = json.dumps(result)
            self.assertNotIn('PRIVATE-', encoded)
            self.assertNotIn('ssid', encoded)
            self.assertNotIn('body_sha256', result)

    def test_expected_missing_icons_are_successful_checks(self):
        result = self.request('/favicon.ico', 'empty', 404)
        self.assertTrue(result['ok'])
        self.assertEqual(result['bytes_received'], 0)

    def test_fixed_workload_outputs_metadata_results_and_summary(self):
        records = []
        summary = workload.run_workload(self.url, 'test-build', cycles=1,
            timeout=0.5, concurrency=2, delay=0, emit=records.append)
        self.assertTrue(summary['ok'])
        self.assertEqual(summary['requests'], sum(len(stage) for stage in workload.STAGES))
        self.assertEqual(records[0]['type'], 'metadata')
        self.assertEqual(records[0]['concurrency'], 2)
        self.assertTrue(records[0]['label_is_operator_supplied'])
        self.assertEqual(records[-1], summary)
        self.assertTrue(all(record['cycle'] == 1 for record in records[1:-1]))
        self.assertNotIn('PRIVATE-', json.dumps(records))

    def test_address_rejects_credentials_dns_and_paths(self):
        for url in ('http://name.local/', 'http://u:p@127.0.0.1/',
                    'http://127.0.0.1:0/',
                    'http://127.0.0.1/api/status', 'https://127.0.0.1/',
                    'http://127.0.0.1/?password=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                workload.device_address(url)


if __name__ == '__main__':
    unittest.main()
