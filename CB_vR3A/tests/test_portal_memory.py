"""Portal lighting must not pull the asynchronous runtime into AP startup."""
import ast
import builtins
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PortalMemoryTest(unittest.TestCase):
    def setUp(self):
        names = ('load_control_modules', 'load_runtime_modules', 'service_portal_lighting')
        nodes = [node for node in ast.parse((ROOT / 'app_main.py').read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        self.ns = dict(gpio=None, ntptime=None, asyncio=None,
                       gc=types.SimpleNamespace(collect=Mock()), debug=Mock(),
                       portal=types.SimpleNamespace(_print_network_heap=Mock()),
                       portal_lighting_active=True, runtime_initialized=False,
                       ambient_controller=None, AmbientLightController=Mock(),
                       state=2, COSYMODE=1, oldHour=-1, current_hour=lambda: 12,
                       timeControl=lambda hour, old: (66, True), cosyLightControl=lambda: 35)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app_main.py', 'exec'), self.ns)
        self.loader = Mock(return_value=object())
        self.modules = {
            'firmware_loader': types.SimpleNamespace(load=self.loader),
            'shared_settings': types.SimpleNamespace(**{name: object() for name in (
                'LightIntensities', 'AmbientLightSettings', 'ECSettings',
                'load_all_settings_from_file', 'update_ambient_status')}),
            'ntptime': types.SimpleNamespace(), 'uasyncio': types.SimpleNamespace(),
        }

    def test_repeated_portal_lighting_does_not_import_ntp_or_asyncio(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name in ('ntptime', 'uasyncio'):
                self.fail('Portal imported ' + name)
            return original(name, *args, **kwargs)
        with patch.dict('sys.modules', self.modules), patch('builtins.__import__', guarded):
            for _ in range(3):
                self.ns['service_portal_lighting']()
        self.loader.assert_called_once_with('gpio')
        self.ns['AmbientLightController'].assert_called_once()
        self.assertEqual(self.ns['ambient_controller'].tick.call_count, 3)
        self.ns['ambient_controller'].tick.assert_called_with(66)
        self.assertIsNone(self.ns['ntptime'])
        self.assertIsNone(self.ns['asyncio'])

    def test_normal_runtime_loads_after_portal_controls_and_remains_idempotent(self):
        with patch.dict('sys.modules', self.modules):
            self.ns['service_portal_lighting']()
            controls = self.ns['gpio']
            self.ns['load_runtime_modules']()
            self.ns['load_runtime_modules']()
        self.assertIs(self.ns['gpio'], controls)
        self.loader.assert_called_once_with('gpio')
        self.assertIs(self.ns['ntptime'], self.modules['ntptime'])
        self.assertIs(self.ns['asyncio'], self.modules['uasyncio'])

    def test_normal_runtime_can_load_without_portal_first(self):
        with patch.dict('sys.modules', self.modules):
            self.ns['load_runtime_modules']()
        self.loader.assert_called_once_with('gpio')
        self.assertIs(self.ns['asyncio'], self.modules['uasyncio'])
