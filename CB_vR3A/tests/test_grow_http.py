import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import test_calibration_portal as portal_test
from test_grow_ui import form, grow_ui
import shared_settings


class GardenHTTPTest(unittest.TestCase):
    setUp = portal_test.CalibrationPortalTest.setUp
    restore = portal_test.CalibrationPortalTest.restore
    request = portal_test.CalibrationPortalTest.request

    def raw_post(self, values):
        body = '&'.join(key+'='+value for key, value in values.items()).encode()
        return b'POST /api/settings HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body

    def test_get_settings_and_post_validation_are_transactional(self):
        body, headers = self.request('/api/settings')
        self.assertIn('lights_on', json.loads(body))
        before = Path('settings.json').read_bytes()
        invalid = form(timezone='Europe/London', ec_low_ms='3', ec_high_ms='2')
        request = self.server.parse_request(self.raw_post(invalid))
        body, headers = self.server.get_response(request)
        self.assertIn(b'400', headers)
        self.assertIn('Low EC alert', json.loads(body.read())['error'])
        self.assertEqual(Path('settings.json').read_bytes(), before)
        request = self.server.parse_request(self.raw_post(form(ambient_enabled='0')))
        body, headers = self.server.get_response(request)
        self.assertIn(b'200', headers)
        self.assertEqual(json.loads(body.read())['message'], 'Settings saved successfully.')
        saved = json.loads(Path('settings.json').read_text())
        self.assertFalse(saved['ambient']['als_enabled'])
        self.assertEqual(saved['ec']['ec_low_alarm_us'], 1200)
        self.assertEqual(saved['t_lights_off'], '21:00')

    def test_status_has_consumer_units_and_no_factory_values(self):
        self.server._read_ec_live = lambda: 1800
        self.server._read_water_live = lambda: dict(valid=True, fill_percent=75, volume_l=8)
        with patch.dict(sys.modules, {'temperature': types.SimpleNamespace(read_celsius=lambda: 22.1)}):
            body, headers = self.request('/api/status')
        data = json.loads(body)
        self.assertIn(b'200', headers)
        self.assertEqual(data['nutrients']['value_ms'], 1.8)
        self.assertEqual(data['water']['fill_percent'], 75)
        self.assertEqual(data['temperature']['value_c'], 22.1)
        for forbidden in ('raw', 'distance_empty_mm', 'distance_cm', 'calibration_a', 'gpio'):
            self.assertNotIn('"'+forbidden+'"', body.decode())

    def test_static_assets_have_correct_types_and_are_streamed(self):
        for path, content_type in (('grow.css', b'text/css'), ('grow.js', b'application/javascript'),
                                   ('icons/moon-svgrepo-com.svg', b'image/svg+xml')):
            file = Path(path)
            file.parent.mkdir(exist_ok=True)
            file.write_text('test asset')
            req = self.server.parse_request(('GET /'+path+' HTTP/1.1\r\n\r\n').encode())
            stream, headers = self.server.get_response(req)
            self.assertIn(content_type, headers)
            self.assertEqual(stream.read(4), b'test')
            self.assertEqual(stream.read(), b' asset')
            stream.close()

    def test_garden_is_the_home_page_in_both_network_modes(self):
        Path('settings.html').write_text('<h1>My Garden</h1>')
        for mode in ('wifi_portal', 'settings', 'sta'):
            self.server = self.portal.HTTPServer(None, '192.168.4.1', mode=mode, listen_sock=object())
            body, headers = self.request('/')
            self.assertIn(b'200', headers)
            self.assertIn(b'My Garden', body)

    def test_fragmented_post_is_read_to_content_length(self):
        raw = self.raw_post(form())
        boundary = raw.index(b'\r\n\r\n')+4
        parts = [raw[:boundary], raw[boundary:boundary+10], raw[boundary+10:]]
        class Socket:
            def __init__(self): self.sent = b''
            def settimeout(self, value): pass
            def recv(self, size): return parts.pop(0) if parts else b''
            def send(self, data): self.sent += bytes(data); return len(data)
        sock = Socket()
        self.server.handle_blocking(sock)
        self.assertIn(b'200 OK', sock.sent)
        self.assertIn(b'Settings saved successfully.', sock.sent)
        self.assertEqual(json.loads(Path('settings.json').read_text())['ec']['ec_high_alarm_us'], 2500)

    def test_wrong_method_and_oversized_body_are_rejected(self):
        request = self.server.parse_request(b'DELETE /api/settings HTTP/1.1\r\n\r\n')
        body, headers = self.server.get_response(request)
        self.assertIn(b'405', headers)
        with self.assertRaises(ValueError):
            self.server.parse_request(b'POST /api/settings HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n'+b'x'*2049)

    def test_disabled_capability_skips_water_and_preserves_other_cards(self):
        self.server._read_ec_live = lambda: 1800
        def forbidden():
            raise AssertionError('Disabled water sensor accessed')
        self.server._read_water_live = forbidden
        with patch('hardware_config.installed', return_value=False), patch.dict(sys.modules, {
                'temperature': types.SimpleNamespace(read_celsius=lambda: 22.1)}):
            body, headers = self.request('/api/status')
        data = json.loads(body)
        self.assertIn(b'200', headers)
        self.assertFalse(data['capabilities']['water_level'])
        self.assertEqual(data['water']['state'], 'DISABLED')
        self.assertEqual(data['water']['status'], 'disabled')
        self.assertEqual(data['temperature']['value_c'], 22.1)
        self.assertEqual(data['nutrients']['value_ms'], 1.8)

    def test_customer_cannot_change_factory_hardware(self):
        import hardware_config
        path = Path('factory_hardware.json')
        path.write_text('{"version":1,"hardware":{"water_level_sensor_installed":false}}')
        before = path.read_bytes()
        request = self.server.parse_request(self.raw_post(form(water_level_sensor_installed='true')))
        body, headers = self.server.get_response(request)
        self.assertIn(b'200', headers)
        self.assertEqual(path.read_bytes(), before)
