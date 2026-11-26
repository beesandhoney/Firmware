"""Exercise normal-mode timing without importing ESP32 startup hardware."""
import ast
import asyncio
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


class WaterTimingTest(unittest.TestCase):
    def namespace(self):
        now = [0]
        clock = types.SimpleNamespace(ticks_ms=lambda: now[0],
                                      ticks_diff=lambda a, b: a - b,
                                      ticks_add=lambda a, b: a + b)
        ns = dict(utime=clock, WATER_DEBUG=True, print=Mock(), ntp_attempt_index=0, next_ntp_retry_ms=None)
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'app_main.py').read_text())
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name in ('sync_time_once', 'report_runtime_delay', 'main_logic')]
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'app_main.py', 'exec'), ns)
        return ns, now

    def test_ntp_duration_is_reported_on_success_and_failure(self):
        for fails in (False, True):
            ns, now = self.namespace()
            def settime():
                now[0] += 40000
                if fails:
                    raise OSError('timeout')
            ns.update(NTP_HOSTS=('test',), NTP_FALLBACK_IPS=('192.0.2.1',),
                      NTP_TIMEOUT_S=2, NTP_RETRY_INTERVAL_S=30, ntp_dns_failed=False, gc=types.SimpleNamespace(collect=lambda: None),
                      ntptime=types.SimpleNamespace(settime=settime))
            self.assertEqual(ns['sync_time_once'](0), not fails)
            ns['print'].assert_any_call('TIMING: NTP blocked runtime for 40000 ms')

    def test_slow_failed_ntp_does_not_retry_immediately_and_water_gap_is_visible(self):
        ns, now = self.namespace()
        attempts = []
        def sync(index):
            attempts.append(now[0])
            now[0] += 40000
            return False
        def read_live():
            now[0] += 95
            return dict(raw=625, depth_mm=100.05, volume_l=6, max_volume_l=12, led_count=4, calibration_state='complete')
        sleeps = [0]
        class Finished(Exception):
            pass
        async def sleep(seconds):
            sleeps[0] += 1
            if sleeps[0] == 3:
                raise Finished()
            now[0] += 50 if sleeps[0] == 1 else 30000
        ns.update(debug=lambda message: None, sync_time_once=sync,
                  NTP_RETRY_INTERVAL_S=30, current_hour=lambda: 12,
                  oldHour=12, state=1, SETTINGS=3, COSYMODE=1, TIMECONTROL=2,
                  in_settings_mode=False, last_in_settings_mode=False, time_synced=False,
                  current_base_brightness=0, current_ec_value=None,
                  long_press_detector=Mock(), on_off_dim_detector=Mock(),
                  ENABLE_STA_HTTP_SERVER=False,
                  portal=types.SimpleNamespace(sta_if=types.SimpleNamespace(isconnected=lambda: True)),
                  cosyLightControl=lambda: 20, ambient_controller=Mock(),
                  update_runtime_status_led=lambda: None, asyncio=types.SimpleNamespace(sleep=sleep))
        with patch('hardware_config.installed', return_value=True), patch.dict('sys.modules', {'volume_http': types.SimpleNamespace(read_live=read_live)}):
            with self.assertRaises(Finished):
                asyncio.run(ns['main_logic']())
        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[1] - (attempts[0] + 40000), 30000)
        messages = [call.args[0] for call in ns['print'].call_args_list]
        self.assertTrue(any('depth=100.05 mm' in message and 'read=95 ms' in message for message in messages))
        self.assertTrue(any('capacity=12 L LED_command=4/8' in message for message in messages))
        self.assertTrue(any('gap=40145 ms' in message for message in messages))


if __name__ == '__main__':
    unittest.main()
