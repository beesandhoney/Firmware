import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class PumpCycleTest(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.period = 1 << 30
        clock = types.SimpleNamespace(
            ticks_ms=lambda: self.now,
            ticks_add=lambda value, delta: (value + delta) % self.period,
            ticks_diff=lambda a, b: (
                (a - b + self.period // 2) % self.period - self.period // 2
            ),
        )
        spec = importlib.util.spec_from_file_location(
            "pump_cycle_under_test", Path(__file__).resolve().parents[1] / "pump_cycle.py"
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"utime": clock}):
            spec.loader.exec_module(module)
        self.controller = module.PumpCycle
        self.outputs = []
        self.pin = types.SimpleNamespace(value=self.outputs.append)

    def test_offline_cycles_without_wall_clock_or_network(self):
        pump = self.controller(self.pin)
        self.assertEqual(self.outputs, [True])
        self.now = 119999
        pump.tick()
        self.assertEqual(self.outputs, [True])
        self.now = 120000
        pump.tick()
        self.assertFalse(pump.is_on)
        self.now = 1319999
        pump.tick()
        self.assertFalse(pump.is_on)
        self.now = 1320000
        pump.tick()
        self.assertEqual(self.outputs, [True, False, True])

    def test_ticks_wrap_during_on_phase(self):
        self.now = self.period - 60000
        pump = self.controller(self.pin)
        self.now = 59999
        pump.tick()
        self.assertTrue(pump.is_on)
        self.now = 60000
        pump.tick()
        self.assertFalse(pump.is_on)

    def test_delayed_service_preserves_full_off_interval(self):
        pump = self.controller(self.pin)
        self.now = 125000
        pump.tick()
        self.now = 1320000
        pump.tick()
        self.assertFalse(pump.is_on)
        self.now = 1325000
        pump.tick()
        self.assertTrue(pump.is_on)


if __name__ == "__main__":
    unittest.main()
