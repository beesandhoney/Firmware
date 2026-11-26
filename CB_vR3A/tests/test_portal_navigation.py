import ast
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch
import test_calibration_portal as calibration_tests
from test_http_recovery import load_method

ROOT = Path(__file__).resolve().parents[1]


class PortalHTTPTest(unittest.TestCase):
    setUp = calibration_tests.CalibrationPortalTest.setUp
    restore = calibration_tests.CalibrationPortalTest.restore
    request = calibration_tests.CalibrationPortalTest.request

    def post(self, path, body=b''):
        req = self.server.parse_request(b'POST '+path+b' HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n'+body)
        body, headers = self.server.get_response(req)
        return json.loads(body.read()), headers

    def test_exit_requires_post_and_does_not_shutdown_in_handler(self):
        callback = Mock()
        self.server.exit_callback = callback
        body, headers = self.request('/api/portal/exit')
        self.assertIn(b'405', headers)
        self.assertFalse(self.server._exit_pending)
        body, headers = self.post(b'/api/portal/exit')
        self.assertIn(b'200', headers)
        self.assertTrue(body['closing'])
        self.assertIsNone(self.server._exit_sent_ms)
        callback.assert_not_called()
        self.assertFalse(self.server.exit_due())

    def test_only_successful_exit_delivery_starts_shutdown_grace(self):
        now = [100]
        self.portal.utime.ticks_ms = lambda: now[0]
        self.portal.utime.ticks_diff = lambda a,b:a-b
        raw = b'POST /api/portal/exit HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 0\r\n\r\n'
        for delivered in (False, True):
            sock = Mock()
            sock.recv.return_value = raw
            with patch.object(self.server, 'write_blocking', return_value=delivered):
                self.server.handle_blocking(sock)
            if not delivered:
                self.assertIsNone(self.server._exit_sent_ms)
            else:
                self.assertEqual(self.server._exit_sent_ms, 100)
                now[0] = 2099
                self.assertFalse(self.server.exit_due())
                now[0] = 2100
                self.assertTrue(self.server.exit_due())

    def test_initial_setup_and_configured_navigation_metadata(self):
        Path('index.html').write_text('Connect your Grow')
        Path('settings.html').write_text('My Garden')
        for mode, main in (('wifi', True), ('settings', True), ('wifi_portal', True)):
            self.server = self.portal.HTTPServer(None, '192.168.4.1', mode=mode, listen_sock=object())
            body, _ = self.request('/')
            self.assertIn(b'My Garden' if main else b'Connect your Grow', body)
            body, _ = self.request('/api/portal')
            self.assertEqual(json.loads(body)['main_available'], main)

    def http_bytes(self, path):
        # Exercise request dispatch too: get_response already returned 404 before
        # the buggy catch-all redirect in handle_blocking was removed.
        output = bytearray()
        sock = Mock()
        sock.recv.return_value = b'GET ' + path + b' HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n'
        def send(data):
            output.extend(data)
            return len(data)
        sock.send.side_effect = send
        self.assertTrue(self.server.handle_blocking(sock))
        return bytes(output)

    def test_missing_icons_and_unknown_paths_do_not_download_dashboard(self):
        for path in (b'/favicon.ico', b'/apple-touch-icon.png',
                     b'/apple-touch-icon-precomposed.png', b'/apple-touch-icon-120x120.png',
                     b'/missing.js', b'/api/missing', b'/not-a-page'):
            with self.subTest(path=path):
                response = self.http_bytes(path)
                headers, body = response.split(b'\r\n\r\n', 1)
                self.assertTrue(headers.startswith(b'HTTP/1.1 404 Not Found'))
                self.assertIn(b'Content-Length: 0', headers)
                self.assertNotIn(b'Location:', headers)
                self.assertEqual(body, b'')

    def test_captive_detection_routes_still_redirect_to_dashboard(self):
        for path in (b'/generate_204', b'/gen_204', b'/hotspot-detect.html', b'/ncsi.txt', b'/redirect'):
            with self.subTest(path=path):
                response = self.http_bytes(path)
                self.assertTrue(response.startswith(b'HTTP/1.1 302 Found'))
                self.assertIn(b'Location: /\r\n', response)
                self.assertTrue(response.endswith(b'\r\n\r\n'))

    def test_network_scan_and_post_credentials(self):
        self.server.networks_getter = lambda: ['Garden & Home', '<test>']
        body, _ = self.request('/api/networks')
        self.assertEqual(json.loads(body)['networks'], ['Garden & Home', '<test>'])
        with patch.object(self.portal, '_get_creds_class') as get_creds:
            body, headers = self.post(b'/api/wifi', b'ssid=Garden+%26+Home&password=abc%2B%2520xyz')
            self.assertIn(b'202', headers)
            get_creds.return_value.assert_called_once_with(ssid=b'Garden & Home', password=b'abc+%20xyz')
            self.assertTrue(self.server.consume_retry_request())
            body, headers = self.post(b'/api/wifi', b'ssid=Home&password=short')
            self.assertIn(b'400', headers)
            self.assertFalse(self.server.consume_retry_request())

    def test_scan_unavailable_failure_and_empty_results_are_distinct(self):
        for getter in (None, Mock(side_effect=OSError('radio busy')), lambda: None):
            self.server.networks_getter = getter
            body, headers = self.request('/api/networks')
            self.assertIn(b'503', headers)
            self.assertIn('error', json.loads(body))
            self.assertNotIn('networks', json.loads(body))
        self.server.networks_getter = lambda: []
        body, headers = self.request('/api/networks')
        self.assertIn(b'200', headers)
        self.assertEqual(json.loads(body), {'networks': []})

    def test_all_navigation_routes_serve_local_pages(self):
        for filename in ('index.html', 'settings.html'):
            Path(filename).write_bytes((ROOT/filename).read_bytes())
        for path in ('/', '/garden', '/settings', '/grow-settings', '/wifi'):
            body, headers = self.request(path)
            self.assertIn(b'200', headers)
            self.assertIn(b'data-grow-style="shared"', body)
        page = (ROOT/'settings.html').read_text()
        for destination in ('/settings', '/grow-settings', '/wifi', '/garden'):
            self.assertIn('href="'+destination+'"', page)
        wifi = (ROOT/'index.html').read_text()
        self.assertIn('href="/settings"', wifi)
        self.assertIn('href="/garden"', wifi)

    def test_static_wifi_assets_stream_with_shared_theme(self):
        for filename in ('index.html', 'wifi.js', 'grow.css'):
            Path(filename).write_bytes((ROOT/filename).read_bytes())
        req = self.server.parse_request(b'GET /wifi HTTP/1.1\r\n\r\n')
        stream, _ = self.server.get_response(req)
        self.assertIn(b'data-grow-style="shared"', stream.read())
        stream.close()
        req = self.server.parse_request(b'GET /wifi.js HTTP/1.1\r\n\r\n')
        stream, headers = self.server.get_response(req)
        self.assertIn(b'application/javascript', headers)
        self.assertTrue(hasattr(stream, 'read'))
        stream.close()


class PortalLifecycleTest(unittest.TestCase):
    def method(self, name, **namespace):
        return load_method('captive_portal.py', 'CaptivePortal', name, namespace)

    def test_shutdown_keeps_sta_and_stops_only_portal_services(self):
        sta, ap, http, dns = Mock(), Mock(), Mock(), Mock()
        portal = types.SimpleNamespace(sta_if=sta, ap_if=ap, http_server=http, dns_server=dns,
                                      _is_sta_connected_with_ip=lambda: True)
        cleanup = self.method('cleanup', gc=types.SimpleNamespace(collect=lambda: None))
        portal.cleanup = lambda keep_sta: cleanup(portal, keep_sta)
        led = Mock()
        self.method('close_portal', status_led=led)(portal)
        sta.disconnect.assert_not_called()
        sta.active.assert_not_called()
        ap.active.assert_called_once_with(False)
        http.stop.assert_called_once()
        dns.stop.assert_called_once_with(None)
        self.assertTrue(portal.portal_closed)
        led.set_wifi_connected.assert_called_once_with(True)

    def test_closed_portal_does_not_restart_http_listener(self):
        portal = types.SimpleNamespace(portal_closed=True, _poll_once=Mock())
        self.method('poll_http')(portal)
        portal._poll_once.assert_not_called()

    def test_connection_service_is_nonblocking_and_keeps_portal_on_success(self):
        now = [0]
        clock = types.SimpleNamespace(ticks_ms=lambda: now[0], ticks_diff=lambda a,b:a-b, time=lambda: 1)
        portal = types.SimpleNamespace(connection_pending=True, _connection_started_ms=0,
            STA_CONNECT_TIMEOUT=10, FAIL_TIMEOUT='timeout', FAIL_AUTH='auth', STATE_STA_CONNECTED='connected',
            _is_sta_connected_with_ip=lambda: False, sta_if=Mock(), creds=Mock(),
            http_server=types.SimpleNamespace(main_available=False, routes={}))
        service = self.method('_service_portal_connection', time=clock, status_led=Mock(), network=types.SimpleNamespace(STAT_WRONG_PASSWORD=-3))
        service(portal)
        self.assertTrue(portal.connection_pending)
        portal._is_sta_connected_with_ip = lambda: True
        service(portal)
        self.assertFalse(portal.connection_pending)
        self.assertTrue(portal.http_server.main_available)
        self.assertEqual(portal.http_server.routes[b'/'], b'./settings.html')
        portal.sta_if.disconnect.assert_not_called()

    def test_real_scan_adapter_preserves_active_setup_ap(self):
        sta, ap = Mock(), Mock()
        sta.scan.return_value = [(b'Home',), (b'Home',), (b'',), (b'Garden & Home',)]
        portal = types.SimpleNamespace(connection_pending=False, _ensure_sta=lambda: True, sta_if=sta, ap_if=ap)
        scan = self.method('scan_networks')
        self.assertEqual(scan(portal), ['Garden & Home', 'Home'])
        sta.active.assert_called_once_with(True)
        sta.scan.assert_called_once()
        self.assertEqual(ap.mock_calls, [])
        portal.connection_pending = True
        with self.assertRaises(OSError):
            scan(portal)
        self.assertEqual(sta.scan.call_count, 1)

    def test_exit_callback_never_resets_or_changes_outputs(self):
        tree = ast.parse((ROOT/'app_main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='exit_settings_callback')
        portal = Mock()
        namespace = dict(portal=portal)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app_main.py', 'exec'), namespace)
        namespace['exit_settings_callback']()
        portal.request_exit.assert_called_once_with()

    def test_offline_settings_exit_starts_all_runtime_tasks_without_reboot(self):
        import sys
        tree = ast.parse((ROOT/'app_main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='run')
        loop, portal, reboot = Mock(), Mock(), Mock()
        portal.captive_portal.return_value = False
        portal.portal_closed = True
        namespace = dict(portal=portal, status_led=Mock(), debug=Mock(), wait_for_boot_hold=Mock(),
                         init_pump=Mock(), settings_mode_requested=lambda: True, clear_settings_flag=Mock(),
                         exit_settings_callback=Mock(), service_startup_controls=Mock(), reboot_clean=reboot,
                         init_runtime_hardware=Mock(), asyncio=types.SimpleNamespace(get_event_loop=lambda: loop),
                         supervise_task=Mock(), main_logic=Mock(), pump_control=Mock(), measure_ec=Mock())
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app_main.py', 'exec'), namespace)
        with patch.dict(sys.modules, {'time': types.SimpleNamespace(sleep_ms=lambda ms: None)}):
            namespace['run']()
        reboot.assert_not_called()
        namespace['init_runtime_hardware'].assert_called_once()
        self.assertEqual(loop.create_task.call_count, 3)
        loop.run_forever.assert_called_once()

    def test_failed_connection_stops_driver_retries_before_rescan(self):
        sta = Mock()
        sta.status.return_value = -3
        portal = types.SimpleNamespace(connection_pending=True, _connection_started_ms=0,
            STA_CONNECT_TIMEOUT=20, FAIL_TIMEOUT='timeout', FAIL_AUTH='auth',
            _is_sta_connected_with_ip=lambda: False, sta_if=sta)
        self.method('_service_portal_connection', time=types.SimpleNamespace(ticks_ms=lambda: 20000, ticks_diff=lambda a,b:a-b),
                    network=types.SimpleNamespace(STAT_WRONG_PASSWORD=-3))(portal)
        self.assertFalse(portal.connection_pending)
        self.assertEqual(portal.last_error, 'auth')
        sta.disconnect.assert_called_once()
        sta.active.assert_not_called()
