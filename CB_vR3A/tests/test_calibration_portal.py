import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault('ujson', json)
import shared_settings


class CalibrationPortalTest(unittest.TestCase):
    def setUp(self):
        self.saved = shared_settings._build_settings_payload()
        self.directory = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(self.restore)
        payload = dict(self.saved)
        payload['morning'] = 73
        payload['water_sensor_type'] = shared_settings.WaterCalibration.SENSOR_TYPE
        payload['water_max_volume_l'] = 12
        payload['water_calibration_point_count'] = 4
        payload['water_calibration'] = [
            {'raw_value': None, 'volume_l': value} for value in (0, 4, 8, 12)
        ]
        Path('settings.json').write_text(json.dumps(payload))
        shared_settings.LightIntensities.settings['morning'] = 1
        shared_settings.WaterCalibration.configure(10)
        aliases = {
            'uerrno': errno, 'uio': io, 'usocket': socket, 'uos': os,
            'utime': types.SimpleNamespace(),
            'machine': types.SimpleNamespace(Pin=object, ADC=object),
            'status_led': types.SimpleNamespace(settings_saved=lambda: None),
            'gpio': types.SimpleNamespace(
                update_water_level_indicator=lambda volume: None,
                read_water_raw=lambda samples, delay: 40.0,
            ),
        }
        self.modules = patch.dict(sys.modules, aliases)
        self.modules.start()
        spec = importlib.util.spec_from_file_location(
            'portal_under_test', Path(__file__).resolve().parents[1] / 'captive_http.py'
        )
        self.portal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.portal)
        self.server = self.portal.HTTPServer(None, '192.168.4.1', mode='settings', listen_sock=object())

    def restore(self):
        if hasattr(self, 'modules'):
            self.modules.stop()
        Path('settings.json').write_text(json.dumps(self.saved))
        shared_settings.load_all_settings_from_file()
        os.chdir(self.old_cwd)
        self.directory.cleanup()

    def request(self, path):
        req = self.server.parse_request(('GET ' + path + ' HTTP/1.1\r\nHost: 192.168.4.1\r\n\r\n').encode())
        body, headers = self.server.get_response(req)
        return body.read(), headers

    def test_portal_starts_and_serves_settings_without_timezone_module(self):
        original_import = __import__
        def guarded(name, *args, **kwargs):
            if name == 'local_time':
                raise ImportError("no module named 'local_time'")
            return original_import(name, *args, **kwargs)
        source = Path(__file__).resolve().parents[1] / 'captive_http.py'
        with patch('builtins.__import__', guarded):
            spec = importlib.util.spec_from_file_location('portal_missing_timezone', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.server = module.HTTPServer(None, '192.168.4.1', mode='settings', listen_sock=object())
            Path('settings.html').write_text('<h1>Light Settings</h1>')
            body, headers = self.request('/settings')
            self.assertIn(b'200 OK', headers)
            self.assertIn(b'Light Settings', body)
            body, headers = self.request('/update_lights?timezone=Europe%2FLondon')
            self.assertIn(b'503 Service Unavailable', headers)
            self.assertIn(b'_fw_*.mpy', body)
            self.assertIn(('Content-Length: %d' % len(body)).encode(), headers)
            body, headers = self.request('/settings')
            self.assertIn(b'200 OK', headers)

    def test_timezone_http_validation_and_persistence(self):
        body, headers = self.request('/update_lights?timezone=Europe%2FLondon')
        self.assertIn(b'200 OK', headers)
        self.assertEqual(json.loads(Path('settings.json').read_text())['timezone'], 'Europe/London')
        body, headers = self.request('/current_settings')
        self.assertEqual(json.loads(body)['clock']['timezone'], 'Europe/London')
        before = Path('settings.json').read_text()
        body, headers = self.request('/update_lights?timezone=Invalid')
        self.assertIn(b'400 Bad Request', headers)
        self.assertEqual(Path('settings.json').read_text(), before)

    def test_pressure_calibration_persists_and_rejects_bad_order(self):
        body, headers = self.request('/configure_water_calibration?max_volume_l=15&point_count=4')
        self.assertIn(b'200 OK', headers)
        for index, raw in enumerate((66, 200, 625, 1180)):
            sys.modules['gpio'].read_water_raw = lambda samples, delay, raw=raw: raw
            body, headers = self.request('/update_cal_point?point=' + str(index))
            self.assertIn(b'200 OK', headers)
        saved = json.loads(Path('settings.json').read_text())
        self.assertEqual(saved['water_calibration'][3]['raw_value'], 1180)
        body, headers = self.request('/update_cal_point?point=1')
        self.assertIn(b'400 Bad Request', headers)
        self.assertEqual(json.loads(Path('settings.json').read_text()), saved)
        body, headers = self.request('/water_level')
        data = json.loads(body)
        self.assertTrue(data['enabled'])
        self.assertEqual(data['volume_l'], 15)
        self.assertAlmostEqual(data['depth_mm'], 199.34, places=2)

    def test_ec_endpoint_powers_and_settles_before_reading(self):
        events = []
        self.portal.setup_adc = lambda: object()
        self.portal.set_ec_power = lambda enabled: events.append(('power', enabled))
        self.portal.utime.sleep_ms = lambda ms: events.append(('settle', ms))
        def read(adc):
            events.append(('read',))
            return 1368.0
        self.portal.read_ec_value = read
        body, headers = self.request('/adc_value?_cb=123')
        self.assertIn(b'200 OK', headers)
        self.assertEqual(float(body), 1368.0)
        self.assertEqual(events, [('power', True), ('settle', 200), ('read',)])

    def test_ec_failure_returns_http_error(self):
        self.portal.setup_adc = lambda: object()
        self.portal.set_ec_power = lambda enabled: None
        self.portal.utime.sleep_ms = lambda ms: None
        def failed(adc):
            raise OSError('ADC failure')
        self.portal.read_ec_value = failed
        body, headers = self.request('/adc_value')
        self.assertIn(b'500 Internal Server Error', headers)


if __name__ == '__main__':
    unittest.main()
