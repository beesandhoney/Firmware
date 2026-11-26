import ast
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.modules.setdefault('ujson', json)
import shared_settings as shared
ROOT = Path(__file__).resolve().parents[1]
with patch.dict(sys.modules, {'utime': types.SimpleNamespace(ticks_ms=lambda: 1000, ticks_diff=lambda a, b: a-b)}):
    import grow_ui
sys.modules['grow_ui'] = grow_ui
import shared_settings as shared
import temperature
from local_time import schedule_phase


def form(**changes):
    data = dict(timezone='Europe/Stockholm', lights_on='05:00', lights_off='21:00',
                ambient_enabled='1', ec_low_ms='1.2', ec_high_ms='2.5')
    data.update(changes)
    return data


class GardenSettingsTest(unittest.TestCase):
    def setUp(self):
        self.before = (dict(shared.LightIntensities.settings), dict(shared.AmbientLightSettings.settings), dict(shared.ECSettings.settings))
        self.directory = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(self.restore)

    def restore(self):
        shared.LightIntensities.settings, shared.AmbientLightSettings.settings, shared.ECSettings.settings = self.before
        os.chdir(self.cwd)
        self.directory.cleanup()

    def test_save_round_trip_and_overnight_control(self):
        with patch.dict(sys.modules, {'status_led': types.SimpleNamespace(settings_saved=lambda: None)}):
            result = grow_ui.save_settings(form(lights_on='21:30', lights_off='05:15', ambient_enabled='0'))
        self.assertEqual(result['message'], 'Settings saved successfully.')
        self.assertEqual(shared.ECSettings.settings['ec_low_alarm_us'], 1200)
        self.assertFalse(shared.AmbientLightSettings.settings['als_enabled'])
        light = shared.LightIntensities.settings
        for minute, on in ((1290, True), (0, True), (314, True), (315, False), (1200, False)):
            self.assertEqual(light[schedule_phase(light, minute)] > 0, on)
        self.assertEqual(grow_ui.next_switch(light, 0), dict(time='05:15', on=False))
        self.assertEqual(grow_ui.next_switch(light, 315), dict(time='21:30', on=True))
        shared.ECSettings.settings['ec_low_alarm_us'] = 99
        shared.load_all_settings_from_file()
        self.assertEqual(shared.ECSettings.settings['ec_low_alarm_us'], 1200)
        self.assertEqual(grow_ui.settings()['lights_on'], '21:30')

    def test_spec_parameter_names_persist_and_restore_without_plant(self):
        data = form()
        del data['ec_low_ms'], data['ec_high_ms']
        data.update(ec_alarm_low='1.0', ec_alarm_high='1.6')
        with patch.dict(sys.modules, {'status_led': types.SimpleNamespace(settings_saved=lambda: None)}):
            result = grow_ui.save_settings(data)['settings']
        self.assertEqual(result['ec_alarm_low'], 1.0)
        self.assertEqual(result['ec_alarm_high'], 1.6)
        self.assertEqual(result['ec_max_ms'], 3.977)
        shared.ECSettings.settings = dict(shared.ECSettings.defaults)
        shared.load_all_settings_from_file()
        self.assertEqual(shared.ECSettings.settings['ec_low_alarm_us'], 1000)
        self.assertEqual(shared.ECSettings.settings['ec_high_alarm_us'], 1600)
        self.assertEqual(grow_ui.settings()['ec_alarm_high'], 1.6)

    def test_supported_sensor_limit_and_threshold_boundaries(self):
        with patch.object(grow_ui, 'save_all_settings_to_file', return_value=True):
            grow_ui.save_settings(form(ec_low_ms='0', ec_high_ms='3.977'))
        self.assertEqual(shared.ECSettings.settings['ec_high_alarm_us'], 3977)
        tree = ast.parse((ROOT / 'gpio.py').read_text())
        points = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'EC_CALIBRATION_POINTS' for t in n.targets))
        self.assertEqual(ast.literal_eval(points)[-1][1] / 1000, shared.ECSettings.MAX_EC_MS)

    def test_invalid_form_never_changes_ram_or_disk(self):
        cases = [form(ec_low_ms='3'), form(ec_high_ms='nan'), form(ec_low_ms='-1'), form(ec_high_ms='101'), form(ec_high_ms='3.978'),
                 form(lights_on='24:00'), form(lights_off='05:00'), form(timezone='Invalid'),
                 form(ambient_enabled='surprise'), form(ec_low_ms='1.00001', ec_high_ms='1.00002')]
        for values in cases:
            with self.assertRaises(ValueError):
                grow_ui.save_settings(values)
            self.assertEqual(shared.LightIntensities.settings, self.before[0])
            self.assertEqual(shared.AmbientLightSettings.settings, self.before[1])
            self.assertEqual(shared.ECSettings.settings, self.before[2])
            self.assertFalse(Path('settings.json').exists())

    def test_failed_commit_preserves_previous_file_and_active_settings(self):
        Path('settings.json').write_text(json.dumps(shared._build_settings_payload()))
        before = Path('settings.json').read_bytes()
        with patch('os.rename', side_effect=OSError('full flash')):
            with self.assertRaises(OSError):
                grow_ui.save_settings(form())
        self.assertEqual(Path('settings.json').read_bytes(), before)
        self.assertEqual(shared.LightIntensities.settings, self.before[0])
        self.assertEqual(shared.AmbientLightSettings.settings, self.before[1])
        self.assertEqual(shared.ECSettings.settings, self.before[2])

    def test_unknown_factory_fields_cannot_change_calibration(self):
        with patch.object(grow_ui, 'save_all_settings_to_file', return_value=True):
            result = grow_ui.save_settings(form(distance_empty_mm='123', bottom_diameter_mm='500'))
        self.assertNotIn('distance_empty_mm', result['settings'])
        self.assertNotIn('bottom_diameter_mm', shared.LightIntensities.settings)

    def test_four_phase_roundtrip_boundaries_and_zero(self):
        data = form(morning_start='23:00', day_start='01:00', evening_start='12:00', night_start='20:00',
                    morning_brightness='0', day_brightness='100', evening_brightness='50')
        with patch.dict(sys.modules, {'status_led': types.SimpleNamespace(settings_saved=lambda: None)}):
            grow_ui.save_settings(data)
        light = shared.LightIntensities.settings
        for minute, phase, brightness in ((1380, 'morning', 0), (0, 'morning', 0), (60, 'daylight', 100),
                                          (720, 'evening', 50), (1200, 'off', 0)):
            self.assertEqual(schedule_phase(light, minute), phase)
            self.assertEqual(light[phase], brightness)
        light['morning'] = 88
        shared.load_all_settings_from_file()
        self.assertEqual(grow_ui.settings()['lighting']['morning']['brightness_percent'], 0)
        saved = dict(light)
        for changes in (dict(day_start='23:00'), dict(day_start='13:00'), dict(morning_brightness='101'),
                        dict(evening_brightness='0.5'), dict(night_brightness='1')):
            with self.assertRaises(ValueError):
                grow_ui.save_settings(dict(data, **changes))
            self.assertEqual(light, saved)


class GardenStatusTest(unittest.TestCase):
    def setUp(self):
        hardware = patch("hardware_config.installed", return_value=True)
        hardware.start()
        self.addCleanup(hardware.stop)

    def status(self, ec=1800, water=None, temp=22):
        gpio = types.SimpleNamespace(last_value=500, fan_pwm_percent=65)
        clock = dict(timezone='Europe/Stockholm', local_time='2026-10-31 12:00:00')
        application = types.SimpleNamespace(runtime_initialized=False, state=2, COSYMODE=1,
                                            portal_lighting_active=True, ambient_controller=None)
        modules = {'gpio': gpio, 'app_main': application, 'temperature': types.SimpleNamespace(read_celsius=lambda: temp)}
        with patch.dict(sys.modules, modules), patch.object(grow_ui, 'clock_status', return_value=clock):
            return grow_ui.status(read_ec=lambda: ec, read_water=lambda: water or dict(valid=True, fill_percent=75, volume_l=8))

    def test_sensor_units_thresholds_and_isolation(self):
        with patch.dict(shared.ECSettings.settings, ec_low_alarm_us=1200, ec_high_alarm_us=2500):
            for value, state in ((1199, 'low'), (1200, 'optimal'), (2500, 'optimal'), (2501, 'high'), (None, 'unavailable'), (float('nan'), 'unavailable')):
                result = self.status(ec=value)
                self.assertEqual(result['nutrients']['status'], state)
                self.assertEqual(result['nutrients']['state'], {'low':'LOW', 'optimal':'NORMAL', 'high':'HIGH', 'unavailable':'FAULT'}[state])
                self.assertEqual(result['water']['status'], 'good')
                self.assertEqual(result['temperature']['value_c'], 22)
            self.assertEqual(self.status()['nutrients']['value_ms'], 1.8)
        self.assertEqual(self.status()['fan']['pwm_percent'], 65)
        for state in ('stale', 'unavailable', 'configuration_fault', 'out_of_range'):
            result = self.status(water=dict(valid=False, status=state, fill_percent=90, volume_l=12), temp=None)
            self.assertIsNone(result['water']['fill_percent'])
            self.assertEqual(result['temperature']['status'], 'unavailable')
        result = self.status(water=dict(valid=True, fill_percent=19, volume_l=2))
        self.assertEqual(result['water']['status'], 'low')

    def test_manual_mode_uses_actual_output_not_schedule(self):
        application = types.SimpleNamespace(runtime_initialized=True, state=1, COSYMODE=1, ambient_controller=None,
                                            MANUAL_OVERRIDE_ENDS='mode_change_or_restart')
        with patch.dict(sys.modules, {'app_main': application, 'gpio': types.SimpleNamespace(last_value=0)}):
            result = grow_ui.lighting_status(dict(local_time='2026-10-31 12:00:00'))
        self.assertFalse(result['on'])
        self.assertEqual(result['mode'], 'manual')
        self.assertEqual(result['override_ends'], 'mode_change_or_restart')
        self.assertIsNone(result['next_switch'])

    def test_actual_outputs_and_phase_transition(self):
        app = types.SimpleNamespace(runtime_initialized=True, state=0, COSYMODE=1, ambient_controller=None)
        gpio = types.SimpleNamespace(last_value=0, fan_pwm_percent=100)
        with patch.dict(sys.modules, {'app_main': app, 'gpio': gpio}):
            result = grow_ui.lighting_status(dict(local_time='2026-10-31 23:00:00'))
            self.assertEqual(result['phase'], 'night')
            self.assertEqual(result['brightness_percent'], 0)
            self.assertEqual(result['next_phase'], 'morning')
            gpio.last_value = None
            result = grow_ui.lighting_status(dict(local_time=None))
            self.assertIsNone(result['phase'])
            self.assertIsNone(result['brightness_percent'])

    def test_gpio_fan_command_tracks_existing_threshold_logic(self):
        tree = ast.parse((ROOT / 'gpio.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'brightnessControl')
        fan = Mock()
        namespace = dict(last_value=None, fan_pwm_percent=None, FAN_ON_PERCENT=30,
                         fan_pin=fan, _ensure_led_pwm=lambda: None, led_pwm_off=lambda: None,
                         pwm_led_1=Mock(), pwm_led_2=Mock())
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'gpio.py', 'exec'), namespace)
        for brightness, pwm in ((0, 0), (29, 0), (30, 100), (100, 100), (0, 0)):
            namespace['brightnessControl'](brightness)
            self.assertEqual(namespace['fan_pwm_percent'], pwm)
            self.assertEqual(namespace['last_value'], brightness * 1023 // 100)
        fan.on.assert_called()
        fan.off.assert_called()

    def test_ec_voltage_calibration_and_interpolation(self):
        tree = ast.parse((ROOT / 'gpio.py').read_text())
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'EC_CALIBRATION_POINTS' for t in n.targets))
                 or (isinstance(n, ast.FunctionDef) and n.name in ('millivolts_to_ec', 'read_ec_value'))]
        namespace = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'gpio.py', 'exec'), namespace)
        convert = namespace['millivolts_to_ec']
        for mv, expected in ((505, 830), (719, 1187), (820, 1500), (1274, 2716), (1588, 3977),
                             (769.5, 1343.5), (0, 0), (1800, 3977)):
            self.assertAlmostEqual(convert(mv), expected)
        adc = types.SimpleNamespace(read=lambda: 1000, read_uv=lambda: 1274000)
        self.assertAlmostEqual(namespace['read_ec_value'](adc), 2716)

    def test_ec_sensor_rails_do_not_appear_as_valid_nutrients(self):
        tree = ast.parse((ROOT/'gpio.py').read_text())
        read = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read_ec_value')
        convert = Mock(return_value=1800)
        namespace = dict(millivolts_to_ec=convert)
        exec(compile(ast.Module(body=[read], type_ignores=[]), 'gpio.py', 'exec'), namespace)
        for raw in (None, 0, 4095):
            self.assertIsNone(namespace['read_ec_value'](types.SimpleNamespace(read=lambda: raw)))
        convert.assert_not_called()
        self.assertEqual(namespace['read_ec_value'](types.SimpleNamespace(read=lambda: 2000, read_uv=lambda: 820000)), 1800)

    def test_temperature_uses_calibrated_voltage_and_median(self):
        adc = Mock()
        adc.read_uv.side_effect = [1650000, 0, 1660000, 1650000, 3300000]
        with patch.object(temperature, '_adc', None), patch.dict(sys.modules, {
                'machine': types.SimpleNamespace(ADC=Mock(return_value=adc, ATTN_11DB=3), Pin=lambda pin: pin)}):
            self.assertEqual(temperature.read_celsius(), 25)
            adc.atten.assert_called_once_with(3)
            self.assertEqual(adc.read_uv.call_count, 5)

    def test_temperature_conversion_and_sensor_rails(self):
        self.assertEqual(temperature.voltage_to_celsius(1650000), 25)
        for expected in (5, 15, 35, 50):
            resistance = 10000 * math.exp(3380 * (1/(expected+273.15)-1/298.15))
            voltage = 3300000 * resistance / (10000+resistance)
            if voltage < 2450000:
                self.assertAlmostEqual(temperature.voltage_to_celsius(voltage), expected, places=1)
        for voltage in (0, 3300000, 2450000, 150000, None, float('nan')):
            self.assertIsNone(temperature.voltage_to_celsius(voltage))

    def test_ambient_adjustment_never_enables_scheduled_night(self):
        tree = ast.parse((ROOT/'app_main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AmbientLightController')
        tick = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'tick')
        ns = dict(AmbientLightSettings=shared.AmbientLightSettings,
                  utime=types.SimpleNamespace(ticks_ms=lambda: 0, ticks_diff=lambda a,b: 0),
                  update_ambient_status=lambda *a: None)
        exec(compile(ast.Module(body=[tick], type_ignores=[]), 'app_main.py', 'exec'), ns)
        controller = types.SimpleNamespace(mode='DIMMED', sensor_fault=False, last_sample_ms=0,
                                           _apply_brightness=lambda value, *a: value)
        with patch.dict(shared.AmbientLightSettings.settings, als_enabled=True, als_dim_level=20):
            self.assertEqual(ns['tick'](controller, 0), 0)
            self.assertEqual(ns['tick'](controller, 10), 10)
            self.assertEqual(ns['tick'](controller, 45), 20)
        with patch.dict(shared.AmbientLightSettings.settings, als_enabled=False):
            self.assertEqual(ns['tick'](controller, 45), 45)
