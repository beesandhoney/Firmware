import ast
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import hardware_config as hardware
import water_config
import factory_calibration
import volume_http

ROOT = Path(__file__).resolve().parents[1]


class HardwareTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in (('CONFIG_FILE', str(Path(directory.name) / 'hardware.json')),
                            ('_config', None), ('_loaded', False), ('_error', None)):
            p = patch.object(hardware, name, value)
            p.start()
            self.addCleanup(p.stop)

    def save(self, installed):
        hardware.save(dict(version=1, hardware=dict(water_level_sensor_installed=installed)))

    def test_missing_corrupt_and_non_boolean_never_assume_a_variant(self):
        self.assertIsNone(hardware.load())
        self.assertIsNotNone(hardware.fault())
        for value in ('{', '{}', '{"version":1,"hardware":{"water_level_sensor_installed":1}}'):
            Path(hardware.CONFIG_FILE).write_text(value)
            self.assertIsNone(hardware.load())
            self.assertIsNone(hardware.installed())

    def test_restart_and_failed_save_preserve_previous_configuration(self):
        self.save(False)
        before = Path(hardware.CONFIG_FILE).read_bytes()
        with patch.object(hardware.os, 'rename', side_effect=OSError('flash full')):
            with self.assertRaises(OSError):
                self.save(True)
        self.assertFalse(hardware.installed())
        self.assertEqual(Path(hardware.CONFIG_FILE).read_bytes(), before)
        hardware.load()
        self.assertFalse(hardware.installed())
        self.save(True)
        hardware.load()
        self.assertTrue(hardware.installed())

    def test_factory_cancel_and_disabled_calibration_never_touch_sensor(self):
        self.save(False)
        before = Path(hardware.CONFIG_FILE).read_bytes()
        with patch('builtins.input', side_effect=['1', '1', 'n', '2', '3', '4']), \
                patch('firmware_loader.load') as load:
            factory_calibration.start()
        load.assert_not_called()
        self.assertEqual(Path(hardware.CONFIG_FILE).read_bytes(), before)

    def test_disabled_and_invalid_hardware_skip_calibration_and_sensor(self):
        with patch.dict(sys.modules, {'utime': types.SimpleNamespace()}), \
                patch('firmware_loader.load') as load, patch.object(water_config, 'calculate') as calculate:
            self.assertEqual(volume_http.read_live()['state'], 'FAULT')
            self.save(False)
            result = volume_http.read_live()
            self.assertEqual(result['state'], 'DISABLED')
            self.assertIsNone(result['configuration_error'])
            self.assertIsNone(result['fill_percent'])
            calculate.assert_not_called()
            load.assert_not_called()

    def test_installed_calibration_and_measurement_faults_and_recovery(self):
        self.save(True)
        gpio = types.SimpleNamespace(read_water_raw=Mock(return_value=None), update_water_level_indicator=Mock(return_value=0))
        clock = types.SimpleNamespace(ticks_ms=lambda: 0, ticks_diff=lambda a,b:a-b)
        with patch.dict(sys.modules, {'gpio': gpio, 'utime': clock}), patch.object(water_config, 'current', return_value=None):
            self.assertEqual(volume_http.read_live()['state'], 'FAULT')
            gpio.read_water_raw.assert_not_called()
        calibration = water_config.create(270, 315, 340, 190)
        with patch.dict(sys.modules, {'gpio': gpio, 'utime': clock}), patch.object(water_config, 'current', return_value=calibration):
            self.assertEqual(volume_http.read_live()['state'], 'FAULT')
            gpio.read_water_raw.return_value = 26.5
            self.assertEqual(volume_http.read_live()['state'], 'ACTIVE')
            gpio.read_water_raw.return_value = None
            result = volume_http.read_live()
            self.assertEqual(result['state'], 'FAULT')
            gpio.update_water_level_indicator.assert_called_with(None)

    def test_gpio_entry_point_cannot_initialize_disabled_sensor(self):
        tree = ast.parse((ROOT / 'gpio.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read_water_raw')
        namespace = dict(water_sensor=None)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'gpio.py', 'exec'), namespace)
        self.save(False)
        self.assertIsNone(namespace['read_water_raw']())
        self.assertIsNone(namespace['water_sensor'])
