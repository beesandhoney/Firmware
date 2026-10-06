import calendar
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.modules.setdefault('ujson', json)
import local_time
import shared_settings
import ast


class LocalTimeTest(unittest.TestCase):
    def test_against_timezone_database_including_dst_boundaries(self):
        for year in (2024, 2025, 2026, 2027, 2028, 2030):
            for month in range(1, 13):
                days = range(25, 32) if month in (3, 10) else (1,)
                for day in days:
                    for hour, minute, second in ((0, 59, 59), (1, 0, 0), (23, 30, 0)):
                        utc = datetime.datetime(year, month, day, hour, minute, second,
                                                tzinfo=datetime.timezone.utc)
                        for zone in local_time.TIMEZONES:
                            expected = utc.astimezone(ZoneInfo(zone))
                            actual = local_time.localtime(zone, utc.timestamp())
                            self.assertEqual(actual[:6], expected.timetuple()[:6], (utc, zone))

    def test_midnight_rolls_into_next_year(self):
        timestamp = calendar.timegm((2026, 12, 31, 23, 30, 0))
        self.assertEqual(local_time.localtime('Europe/Stockholm', timestamp)[:6],
                         (2027, 1, 1, 0, 30, 0))

    def test_epoch_independence(self):
        epoch = calendar.timegm((2000, 1, 1, 0, 0, 0))
        timestamp = calendar.timegm((2026, 3, 29, 1, 0, 0)) - epoch
        with patch.object(local_time.time, 'gmtime', side_effect=lambda value: original(value + epoch)):
            self.assertEqual(local_time.localtime('Europe/Stockholm', timestamp)[:6],
                             (2026, 3, 29, 3, 0, 0))

    def test_invalid_clock_and_zone_fallback(self):
        with patch.object(local_time.time, 'time', return_value=0):
            self.assertIsNone(local_time.status()['local_time'])
        self.assertEqual(local_time.normalize_timezone('unknown'), 'Europe/Stockholm')

    def test_schedule_time_validation(self):
        for value in ('00:00', '07:00', '23:59'):
            self.assertTrue(local_time.valid_schedule_time(value))
        for value in ('24:00', '07:60', 'x7:00', '7:00', None):
            self.assertFalse(local_time.valid_schedule_time(value))

    def test_schedule_minutes_and_overnight(self):
        settings = dict(shared_settings.LightIntensities.settings)
        for minute, phase in ((449, 'off'), (450, 'morning'), (540, 'daylight'),
                              (1080, 'evening'), (1320, 'off')):
            self.assertEqual(local_time.schedule_phase(settings, minute), phase)
        settings.update(t_morning_start='22:00', t_day_start='23:00',
                        t_evening_start='05:00', t_lights_off='07:00')
        self.assertEqual(local_time.schedule_phase(settings, 60), 'daylight')
        self.assertEqual(local_time.schedule_phase(settings, 420), 'off')

    def test_persistent_timezone_and_legacy_file(self):
        saved = dict(shared_settings.LightIntensities.settings)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'settings.json')
                shared_settings.LightIntensities.update_values(timezone='Europe/Lisbon')
                self.assertTrue(shared_settings.LightIntensities.save_settings_to_file(path))
                shared_settings.LightIntensities.update_values(timezone='Europe/London')
                shared_settings.load_all_settings_from_file(path)
                self.assertEqual(shared_settings.LightIntensities.settings['timezone'], 'Europe/Lisbon')
                shared_settings.LightIntensities.settings['timezone'] = local_time.DEFAULT_TIMEZONE
                Path(path).write_text('{"morning": 20}')
                shared_settings.load_all_settings_from_file(path)
                self.assertEqual(shared_settings.LightIntensities.settings['timezone'], 'Europe/Stockholm')
        finally:
            shared_settings.LightIntensities.settings = saved

    def test_runtime_preserves_unsynced_fallback_and_uses_saved_minutes(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'app_main.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'timeControl')
        namespace = {'time_synced': False, 'LightIntensities': shared_settings.LightIntensities}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'main.py', 'exec'), namespace)
        control = namespace['timeControl']
        self.assertEqual(control(5, 4)[0], shared_settings.LightIntensities.settings['morning'])
        self.assertEqual(control(20, 19)[0], shared_settings.LightIntensities.settings['off'])
        namespace['time_synced'] = True
        self.assertEqual(control(7 + 29 / 60, 7)[0], shared_settings.LightIntensities.settings['off'])
        self.assertEqual(control(7.5, 7)[0], shared_settings.LightIntensities.settings['morning'])


original = local_time.time.gmtime
