import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

sys.modules.setdefault('ujson', json)

import water_config as config
import factory_calibration as factory


def calibrated():
    return config.create(270, 315, 340, 190)


class WaterCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.hardware = patch("hardware_config.installed", return_value=True)
        self.hardware.start()
        self.addCleanup(self.hardware.stop)

    def test_old_or_incomplete_config_module_stops_before_prompts_and_measurements(self):
        for old in (types.SimpleNamespace(__file__='_fw_water_config.mpy'),
                    types.SimpleNamespace(FORMAT_VERSION=1, create=lambda: None),
                    types.SimpleNamespace(FORMAT_VERSION=2),
                    types.SimpleNamespace(FORMAT_VERSION=2, create=None)):
            with patch.object(factory, 'config', old), patch('builtins.input') as prompt, \
                    patch('builtins.print') as output, patch('firmware_loader.load') as load, \
                    patch.object(factory, 'measure') as measure:
                factory._water_menu()
                self.assertIsNone(factory.calibrate(object()))
                prompt.assert_not_called()
                load.assert_not_called()
                measure.assert_not_called()
                output.assert_any_call('_fw_water_config.mpy and _fw_volume_http.mpy together.')
                output.assert_any_call('Then press Ctrl-D to restart, Ctrl-C to stop the application, and start calibration again.')

    def test_version_two_schema_and_derived_geometry(self):
        c = calibrated()
        self.assertEqual(set(c), {'version', 'water_level'})
        self.assertEqual(c['version'], 2)
        w = c['water_level']
        self.assertEqual(set(w), {'bottom_diameter_mm', 'full_diameter_mm', 'distance_empty_mm',
                                 'distance_full_mm', 'effective_height_mm', 'max_volume_liters', 'calibrated'})
        self.assertEqual(w['effective_height_mm'], 150)
        expected = math.pi * 150 * (270**2 + 270*315 + 315**2) / 12000000
        self.assertAlmostEqual(w['max_volume_liters'], expected)
        self.assertIs(config.validate(c), c)

    def test_normal_height_volume_and_fill_follow_specification(self):
        c = calibrated()
        empty, half, full = [config.calculate(d, c) for d in (34, 26.5, 19)]
        self.assertEqual(empty['fill_percent'], 0)
        self.assertEqual(full['fill_percent'], 100)
        self.assertEqual(half['depth_mm'], 75)
        diameter = 270 + (315-270)*75/150
        expected_volume = math.pi * 75 * (270**2 + 270*diameter + diameter**2) / 12000000
        self.assertAlmostEqual(half['volume_l'], expected_volume)
        self.assertAlmostEqual(half['fill_percent'], 100*expected_volume/full['volume_l'])
        self.assertLess(half['fill_percent'], 50)
        self.assertTrue(all(r['valid'] for r in (empty, half, full)))
        cylinder = config.create(300, 300, 340, 190)
        self.assertAlmostEqual(config.calculate(26.5, cylinder)['fill_percent'], 50)

    def test_out_of_range_detected_before_clamping(self):
        for distance, height, percent in ((40, -60, 0), (15, 190, 100)):
            result = config.calculate(distance, calibrated())
            self.assertEqual(result['status'], 'out_of_range')
            self.assertFalse(result['valid'])
            self.assertEqual(result['unclamped_height_mm'], height)
            self.assertEqual(result['fill_percent'], percent)
            self.assertEqual(result['depth_mm'], 0 if percent == 0 else 150)
        for distance in (None, 0, 401, float('nan'), float('inf'), True):
            result = config.calculate(distance, calibrated())
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['volume_l'])

    def test_invalid_calibration_inputs_and_configured_limits(self):
        for value in (0, -1, float('nan'), float('inf'), True):
            for bottom, full in ((value, 315), (270, value)):
                with self.assertRaises(ValueError):
                    config.create(bottom, full, 340, 190)
        for empty, full in ((340, 340), (190, 340), (340, 290), (340, 290.1), (4001, 190), (340, 19)):
            with self.assertRaises(ValueError):
                config.create(270, 315, empty, full)
        self.assertEqual(config.create(300, 300, 340, 240)['water_level']['effective_height_mm'], 100)
        for full in (289.9, 265, 241):
            shallow = config.create(300, 300, 340, full)
            self.assertAlmostEqual(shallow['water_level']['effective_height_mm'], 340 - full)
            config.validate(json.loads(json.dumps(shallow)))
            self.assertTrue(config.calculate((340 + full) / 20, shallow)['valid'])
        self.assertEqual(config.create(300, 300, 4000, 20)['water_level']['effective_height_mm'], 3980)
        for key, value in (('MIN_EFFECTIVE_HEIGHT_MM', 151), ('MAX_EFFECTIVE_HEIGHT_MM', 149),
                           ('MIN_VOLUME_LITERS', 11), ('MAX_VOLUME_LITERS', 9)):
            with patch.object(config, key, value), self.assertRaises(ValueError):
                calibrated()
        for diameter in (1, 100000):
            with self.assertRaises(ValueError):
                config.create(diameter, diameter, 340, 190)

    def test_invalid_or_legacy_configuration_never_reports_calibrated_level(self):
        cases = [None, {}, [], dict(version=1, container={}), dict(version=2, water_level={})]
        for key, bad in (('effective_height_mm', 151), ('max_volume_liters', 999),
                         ('bottom_diameter_mm', '270'), ('full_diameter_mm', float('nan')),
                         ('distance_empty_mm', True), ('calibrated', 'true'), ('calibrated', False)):
            c = calibrated()
            c['water_level'][key] = bad
            cases.append(c)
        for c in cases:
            with patch.object(config, 'current', return_value=c):
                result = config.calculate(26.5)
            self.assertEqual(result['status'], 'configuration_fault')
            self.assertFalse(result['valid'])
            self.assertIsNone(result['volume_l'])
            self.assertIsNone(result['fill_percent'])

    def test_derived_values_allow_serialization_rounding_only(self):
        c = calibrated()
        c['water_level']['max_volume_liters'] = round(c['water_level']['max_volume_liters'], 5)
        config.validate(c)
        self.assertEqual(config.calculate(19, c)['fill_percent'], 100)
        c['water_level']['max_volume_liters'] += 0.01
        with self.assertRaises(ValueError):
            config.validate(c)

    def test_median_stability_and_sample_validity(self):
        sensor = MagicMock()
        sensor.read_once_cm.side_effect = [34]*24 + [None]*6
        self.assertEqual(factory.measure(sensor), 340)
        self.assertEqual(sensor.read_once_cm.call_count, 30)
        sensor.read_once_cm.side_effect = [34]*15 + [35]*15
        self.assertEqual(factory.measure(sensor), 345)
        for readings in ([34]*23+[None]*7, [34]*29+[35.01], [None]*30):
            sensor.read_once_cm.side_effect = readings
            with self.assertRaises(ValueError):
                factory.measure(sensor)

    def test_each_reference_can_be_retried_without_reentering_diameter(self):
        replies = ['270', '', 'r', '', '315', '', 'r', '', 'y']
        with patch('builtins.input', side_effect=replies) as prompt, patch('builtins.print'), \
                patch.object(factory, 'measure', side_effect=[ValueError('unstable'), 340, OSError('timeout'), 190]) as measure:
            result = factory.calibrate(object())
        self.assertEqual(result, calibrated())
        self.assertEqual(measure.call_count, 4)
        self.assertEqual(sum('diameter (' in call.args[0] or 'water level (mm)' in call.args[0]
                             for call in prompt.call_args_list), 2)
        self.assertFalse(any('height' in call.args[0].lower() for call in prompt.call_args_list))

    def test_invalid_reference_pair_allows_redoing_either_point(self):
        for redo in (1, 2):
            replies = ['270', '', '315', '', str(redo), '270' if redo == 1 else '315', '', 'y']
            distances = [190, 190, 340] if redo == 1 else [340, 340, 190]
            with patch('builtins.input', side_effect=replies), patch('builtins.print'), \
                    patch.object(factory, 'measure', side_effect=distances) as measure:
                result = factory.calibrate(object())
            self.assertEqual(result, calibrated())
            self.assertEqual(measure.call_count, 3)

    def test_point_can_edit_diameter_or_cancel(self):
        with patch('builtins.input', side_effect=['-1', 'e', '270', '', 'e', '275', '']), \
                patch('builtins.print'), patch.object(factory, 'measure', side_effect=[ValueError('unstable'), 340]):
            point = factory.capture_point(object(), 1)
        self.assertEqual(point, dict(diameter_mm=275, distance_mm=340))
        with patch('builtins.input', side_effect=['315', '', 'c']), patch('builtins.print'), \
                patch.object(factory, 'measure', side_effect=ValueError('unstable')):
            self.assertIsNone(factory.capture_point(object(), 2))

    def run_repl(self, replies, distances, saved=None):
        with patch('firmware_loader.load'), patch.object(config, 'current', return_value=saved), \
                patch('builtins.input', side_effect=replies) as prompt, patch('builtins.print') as output, \
                patch.object(factory, 'measure', side_effect=distances), patch.object(config, 'save') as save:
            factory._water_menu()
        return prompt, output, save

    def test_repl_saves_version_two_without_height_entry_or_mandatory_third_point(self):
        replies = ['1', '270', '', '315', '', 'y', '4', 'y', '5']
        prompt, output, save = self.run_repl(replies, [340, 190])
        save.assert_called_once_with(calibrated())
        self.assertFalse(any('height' in call.args[0].lower() for call in prompt.call_args_list))
        output.assert_any_call('Calculated effective height: 150.0 mm')
        output.assert_any_call('Empty distance (0%): 340.0 mm')
        output.assert_any_call('Full distance (100%): 190.0 mm')
        output.assert_any_call('Calibration is pending, NOT saved. Choose 4 Save to keep it after restart.')
        output.assert_any_call('Factory configuration saved.')

    def test_exit_after_accepting_points_explains_that_calibration_was_not_saved(self):
        _, output, save = self.run_repl(['1', '270', '', '315', '', 'y', '5'], [340, 190])
        save.assert_not_called()
        output.assert_any_call('Unsaved calibration discarded; stored calibration unchanged.')

    def test_cancel_exit_and_failed_reference_keep_previous_configuration(self):
        saved = calibrated()
        scenarios = [(['1', '270', '', '315', '', 'n', '5'], [350, 200]),
                     (['1', '270', '', '315', '', 'y', '5'], [350, 200]),
                     (['1', '270', '', '315', '', 'y', '4', 'n', '5'], [350, 200]),
                     (['1', '270', '', '315', '', 'c', '5'], [350, ValueError('unstable')])]
        for replies, readings in scenarios:
            _, _, save = self.run_repl(replies, readings, saved)
            save.assert_not_called()
            self.assertEqual(saved, calibrated())
        # Cancelling a failed replacement still leaves the prior draft intact.
        _, _, save = self.run_repl(['1', '270', '', '315', '', 'c', '4', 'y', '5'],
                                  [350, ValueError('unstable')], saved)
        save.assert_called_once_with(saved)

    def test_optional_verification_tolerance_and_failed_save_block(self):
        for distance, passed in ((255, True), (254.9, False)):
            replies = ['3', '75', '4'] + (['y'] if passed else []) + ['5']
            saved = calibrated()
            _, _, save = self.run_repl(replies, [distance], saved)
            self.assertEqual(save.call_count, 1 if passed else 0)
            self.assertEqual(saved, calibrated())
        # A display-only measurement does not erase a failed independent check.
        _, _, save = self.run_repl(['3', '75', '3', '', '4', '5'], [250, 265], calibrated())
        save.assert_not_called()
        # A successful independent retry allows saving again.
        _, _, save = self.run_repl(['3', '75', '3', '75', '4', 'y', '5'], [250, 265], calibrated())
        save.assert_called_once_with(calibrated())

    def test_storage_reload_and_interrupted_commit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'CONFIG_FILE', directory+'/factory.json'):
            try:
                config.save(calibrated())
                config._config, config._loaded = None, False
                self.assertEqual(config.current(), calibrated())
                before = Path(config.CONFIG_FILE).read_bytes()
                changed = config.create(270, 315, 350, 200)
                with patch.object(config.os, 'rename', side_effect=OSError('power failure')):
                    with self.assertRaises(OSError):
                        config.save(changed)
                self.assertEqual(Path(config.CONFIG_FILE).read_bytes(), before)
                self.assertEqual(config.load(), calibrated())
                with patch.object(config.json, 'dump', side_effect=OSError('write failure')):
                    with self.assertRaises(OSError):
                        config.save(changed)
                self.assertEqual(Path(config.CONFIG_FILE).read_bytes(), before)
                for contents in ('{', '{}', '{"version": 1}', '{"version": 2, "water_level": {}}'):
                    Path(config.CONFIG_FILE).write_text(contents)
                    self.assertIsNone(config.load())
                    self.assertEqual(config.calculate(26.5)['status'], 'configuration_fault')
                Path(config.CONFIG_FILE).unlink()
                self.assertIsNone(config.load())
            finally:
                config._loaded = False
                config._config = None

    def test_load_failure_reason_survives_until_successful_save_or_reload(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'CONFIG_FILE', directory+'/factory.json'), \
                patch.object(config, '_config', None), patch.object(config, '_loaded', False), \
                patch.object(config, '_load_error', None), patch('builtins.print') as output:
            self.assertIsNone(config.load())
            reason = config.calculate(26.5)['configuration_error']
            self.assertIn('Calibration file missing:', reason)
            output.assert_any_call('WATER calibration fault:', reason)
            Path(config.CONFIG_FILE).write_text('{"version": 1}')
            self.assertIsNone(config.load())
            self.assertIn('Version 2 factory calibration required', config.calculate(26.5)['configuration_error'])
            invalid = calibrated()
            invalid['water_level']['max_volume_liters'] = 99
            Path(config.CONFIG_FILE).write_text(json.dumps(invalid))
            self.assertIsNone(config.load())
            self.assertIn('Inconsistent stored max_volume_liters', config.calculate(26.5)['configuration_error'])
            config.save(calibrated())
            self.assertIsNone(config.calculate(26.5)['configuration_error'])
            self.assertTrue(config.calculate(26.5)['valid'])
            config._config, config._loaded = None, False
            self.assertEqual(config.load(), calibrated())
            self.assertIsNone(config.calculate(26.5)['configuration_error'])

    def test_stale_timeout_and_invalid_configuration(self):
        import volume_http
        now = [0]
        gpio = types.SimpleNamespace(read_water_raw=MagicMock(side_effect=[26.5, None, None]),
                                     update_water_level_indicator=lambda v: 0)
        with patch.object(config, 'current', return_value=calibrated()), patch.dict(sys.modules, {
            'gpio': gpio, 'utime': types.SimpleNamespace(ticks_ms=lambda: now[0], ticks_diff=lambda a,b:a-b)}):
            self.assertEqual(volume_http.read_live()['status'], 'valid')
            now[0] = 100
            self.assertEqual(volume_http.read_live()['status'], 'stale')
            now[0] = 10001
            self.assertIsNone(volume_http.read_live()['volume_l'])
            for invalid in (None, {}, dict(version=1)):
                gpio.read_water_raw.side_effect = None
                gpio.read_water_raw.return_value = 26.5
                with patch.object(config, 'current', return_value=invalid):
                    result = volume_http.read_live()
                self.assertEqual(result['calibration_state'], 'fault')
                self.assertFalse(result['valid'])
                self.assertIsNone(result['volume_l'])

    def test_led_bar_uses_recommended_capacity(self):
        with patch.object(config, 'current', return_value=calibrated()), patch.dict(sys.modules, {
            'machine': types.SimpleNamespace(Pin=MagicMock(), PWM=MagicMock(), ADC=MagicMock()),
            'utime': types.SimpleNamespace(),
            'level_indicator': types.SimpleNamespace(LevelIndicator=MagicMock()),
        }):
            spec = importlib.util.spec_from_file_location('gpio_test', Path(__file__).resolve().parents[1] / 'gpio.py')
            gpio = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gpio)
            capacity = config.calculate(None)['max_volume_l']
            for fraction, count in ((0,0), (0.01,1), (0.5,4), (1,8)):
                self.assertEqual(gpio.update_water_level_indicator(capacity*fraction), count)
            self.assertEqual(gpio.update_water_level_indicator(None), 0)
            with patch.object(config, 'current', return_value=None):
                self.assertEqual(gpio.update_water_level_indicator(5), 0)

    def test_both_web_servers_and_ui_have_no_calibration_controls(self):
        root = Path(__file__).resolve().parents[1]
        for file in ('web_server.py', 'captive_http.py', 'settings.html'):
            source = (root/file).read_text()
            for route in ('/configure_water_calibration', '/update_cal_point'):
                self.assertNotIn(route, source)

    def test_ultrasonic_pins_filter_and_timeouts(self):
        now = [0]
        pins = []
        def pin(number, mode, **kwargs):
            pins.append((number, mode))
            return MagicMock()
        pin.OUT, pin.IN = 1, 0
        pulse = MagicMock(side_effect=[1000, 2000, 3000, -2, OSError(), 0])
        def sleep(ms): now[0] += ms
        with patch.dict(sys.modules, {
            'machine': types.SimpleNamespace(Pin=pin, time_pulse_us=pulse),
            'utime': types.SimpleNamespace(sleep_ms=sleep, sleep_us=lambda us: None,
                       ticks_ms=lambda: now[0], ticks_diff=lambda a,b: a-b),
        }):
            spec = importlib.util.spec_from_file_location('ultrasonic_under_test', Path(__file__).resolve().parents[1] / 'water_level.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sensor = module.WaterLevelSensor()
            self.assertEqual(pins, [(27, 1), (33, 0)])
            self.assertAlmostEqual(sensor.read_raw(), 34.3)
            self.assertIsNone(sensor.read_raw())
            self.assertGreaterEqual(now[0], 300)

    def capture_debug(self, readings, calibration, samples=None, **options):
        sensor = MagicMock()
        sensor.read_once_cm.side_effect = readings
        clock = types.SimpleNamespace(ticks_ms=lambda: 0, ticks_diff=lambda a, b: a-b,
                                      sleep_ms=MagicMock())
        with patch.dict(sys.modules, {'utime': clock}), patch('builtins.print') as output, \
                patch('firmware_loader.load', return_value=types.SimpleNamespace(WaterLevelSensor=lambda: sensor)), \
                patch.object(config, 'current', return_value=calibration), patch.object(config, 'save') as save:
            factory.debug(samples=samples, **options)
            save.assert_not_called()
        lines = [call.args[0] for call in output.call_args_list]
        rows = [line.split(',') for line in lines if line[0].isdigit()]
        return rows, lines, clock

    def test_debug_reports_raw_fluctuations_and_invalid_echoes(self):
        rows, lines, clock = self.capture_debug([34, None, 40], calibrated(), samples=3, interval_ms=1, csv=True)
        self.assertEqual(rows[0][2:9], ['340.0', '0.0', '--', '340.0', '340.0', '340.0', '0.0'])
        self.assertEqual(rows[1][2:5], ['--', '--', '--'])
        self.assertEqual(rows[1][-2:], ['1/2', 'invalid_echo'])
        # Show negative height and the raw jump, even though the display would clamp.
        self.assertEqual(rows[2][2:9], ['400.0', '-60.0', '60.0', '370.0', '340.0', '400.0', '60.0'])
        self.assertEqual(rows[2][-1], 'out_of_range')
        self.assertEqual(clock.sleep_ms.call_count, 2)
        clock.sleep_ms.assert_called_with(60)
        rows, lines, clock = self.capture_debug([34, None, 40], calibrated(), samples=3)
        self.assertIn('[3 | 0.0 s] Distance: 400.0 mm | Height: -60.0 mm | Change: 60.0 mm', lines)
        self.assertIn('  Last 3 readings (mm): min 340.0 | max 400.0 | median 370.0 | spread 60.0', lines)
        self.assertIn('  Invalid echoes: 1/3 | Status: out_of_range\n', lines)
        self.assertEqual(clock.sleep_ms.call_count, 2)
        clock.sleep_ms.assert_called_with(1000)

    def test_debug_window_expires_old_outliers_and_works_without_calibration(self):
        rows, lines, clock = self.capture_debug([40, None] + [30]*30, None, samples=32, csv=True)
        self.assertEqual(rows[-1][2:4], ['300.0', '--'])
        self.assertEqual(rows[-1][5:], ['300.0', '300.0', '300.0', '0.0', '0/30', 'uncalibrated'])

    def test_debug_ctrl_c_returns_to_calibration_menu(self):
        with patch.dict(sys.modules, {'utime': types.SimpleNamespace(ticks_ms=lambda: 0)}), \
                patch('firmware_loader.load') as load, patch.object(config, 'current', return_value=calibrated()), \
                patch('builtins.input', side_effect=['6', '2', '5']) as prompt, patch('builtins.print') as output:
            load.return_value.WaterLevelSensor.return_value.read_once_cm.side_effect = KeyboardInterrupt
            factory._water_menu()
            self.assertEqual(prompt.call_count, 3)
            output.assert_any_call('Water debug stopped.')
            self.assertFalse(any('Cancelled' in str(call) for call in output.call_args_list))
