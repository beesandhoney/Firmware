import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import firmware_loader


class FirmwareLoaderTest(unittest.TestCase):
    def test_registers_bytecode_under_public_name_once(self):
        module = types.ModuleType('_fw_example')
        with patch.dict(sys.modules, {'_fw_example': module}):
            self.assertIs(firmware_loader.load('example'), module)
            self.assertIs(sys.modules['example'], module)
            self.assertIs(firmware_loader.load('example'), module)

    def test_missing_bytecode_does_not_compile_source(self):
        with self.assertRaises(ImportError):
            firmware_loader.load('missing_test_firmware')
        self.assertNotIn('missing_test_firmware', sys.modules)

    def test_reuses_dependency_already_loaded(self):
        module = types.ModuleType('example')
        with patch.dict(sys.modules, {'example': module}):
            self.assertIs(firmware_loader.load('example'), module)

    def test_generated_bytecode_matches_sources_and_device_format(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'firmware_build.json').read_text())
        self.assertIn('v1.28.0', manifest['compiler'])
        for name, expected in manifest['modules'].items():
            source = (root / (name + '.py')).read_bytes()
            bytecode = (root / ('_fw_' + name + '.mpy')).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected['source_sha256'], name)
            self.assertEqual(hashlib.sha256(bytecode).hexdigest(), expected['bytecode_sha256'], name)
            self.assertEqual(bytecode[:3], b'M\x06\x00', name)
            self.assertLessEqual(bytecode[3], 31, name)
