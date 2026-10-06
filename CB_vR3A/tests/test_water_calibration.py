import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

sys.modules.setdefault('ujson', json)
from shared_settings import WaterCalibration, raw_to_liters, _load_water_calibration
import volume_http


class WaterCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.saved = (WaterCalibration.max_volume_l, WaterCalibration.point_count,
                      WaterCalibration.to_serializable())
        WaterCalibration.configure(12)
        self.addCleanup(self.restore)

    def restore(self):
        WaterCalibration.max_volume_l, WaterCalibration.point_count, WaterCalibration.points = self.saved

    def calibrate(self):
        for index, raw in enumerate((66, 200, 625, 1180)):
            self.assertTrue(WaterCalibration.update_point(index, raw))

    def test_interpolation_and_clamping(self):
        self.calibrate()
        for raw, expected in ((0, 0), (66, 0), (133, 2), (412.5, 6), (902.5, 10), (1180, 12), (4095, 12)):
            self.assertEqual(raw_to_liters(raw), expected)
        self.assertEqual(WaterCalibration.status()['state'], 'complete')

    def test_invalid_and_incomplete_calibration(self):
        self.assertIsNone(raw_to_liters(200))
        self.assertTrue(WaterCalibration.update_point(0, 66))
        for raw in (66, 50, float('nan'), float('inf'), 4096):
            self.assertFalse(WaterCalibration.update_point(1, raw))
        self.calibrate()
        WaterCalibration.points[2]['raw_value'] = 10
        self.assertEqual(WaterCalibration.status()['state'], 'invalid')
        self.assertIsNone(raw_to_liters(200))

    def test_legacy_sensor_is_reset(self):
        self.calibrate()
        _load_water_calibration({'water_sensor_type': 'ultrasonic_distance_cm',
                                 'water_max_volume_l': 15, 'water_calibration': WaterCalibration.points})
        self.assertEqual(WaterCalibration.max_volume_l, 15)
        self.assertEqual(WaterCalibration.status()['recorded'], 0)

    def test_configuration_and_save_failure(self):
        self.calibrate()
        previous = WaterCalibration.to_serializable()
        for capacity in ('nan', 'inf', '-1', '0', '10001'):
            self.assertIn(b'400', volume_http.configure({'max_volume_l': capacity})[1])
        with patch.object(volume_http, 'save_all_settings_to_file', return_value=False):
            self.assertIn(b'500', volume_http.configure({'max_volume_l': '20'})[1])
        self.assertEqual(WaterCalibration.points, previous)

    def test_led_bar_tracks_volume_and_clears_on_missing_reading(self):
        with patch.dict(sys.modules, {
            'machine': types.SimpleNamespace(Pin=MagicMock(), PWM=MagicMock(), ADC=MagicMock()),
            'utime': types.SimpleNamespace(),
            'level_indicator': types.SimpleNamespace(LevelIndicator=MagicMock()),
        }):
            spec = importlib.util.spec_from_file_location('gpio_test', Path(__file__).resolve().parents[1] / 'gpio.py')
            gpio = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gpio)
            for volume, count in ((0, 0), (0.1, 1), (2.99, 1), (3, 2), (6, 4), (12, 8), (20, 8)):
                self.assertEqual(gpio.update_water_level_indicator(volume), count)
                gpio.level_indicator.bar.assert_called_with(count)
            self.assertEqual(gpio.update_water_level_indicator(None), 0)
            gpio.level_indicator.clear.assert_called_once()

    def test_adc_configuration_average_and_depth(self):
        class ADC:
            ATTN_11DB = 3
            WIDTH_12BIT = 12
            def __init__(self, pin): self.pin = pin
            def atten(self, value): self.attenuation = value
            def width(self, value): self.bits = value
            def read(self): return 625
        delays = []
        with patch.dict(sys.modules, {'machine': types.SimpleNamespace(Pin=lambda pin: pin, ADC=ADC),
                                    'utime': types.SimpleNamespace(sleep_ms=delays.append)}):
            spec = importlib.util.spec_from_file_location('pressure_test', Path(__file__).resolve().parents[1] / 'water_level.py')
            sensor_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sensor_module)
            sensor = sensor_module.WaterLevelSensor()
            self.assertEqual((sensor.adc.pin, sensor.adc.attenuation, sensor.adc.bits), (33, 3, 12))
            self.assertEqual(sensor.read_raw(), 625)
            self.assertEqual(delays, [5] * 19)
            self.assertAlmostEqual(sensor_module.raw_to_depth_mm(625), 100.05, places=2)
            self.assertEqual(sensor_module.raw_to_depth_mm(0), 0)
            self.assertIsNone(sensor_module.raw_to_depth_mm(None))


if __name__ == '__main__':
    unittest.main()
