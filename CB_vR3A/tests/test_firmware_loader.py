import builtins
import hashlib
import json
from pathlib import Path
import sys
import types
import tempfile
import zipfile
import unittest
from unittest.mock import Mock, patch

import firmware_loader
import build_firmware


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


class LoaderHarness:
    """Model import caching and path precedence without changing host sys.path."""
    def __init__(self, frozen=None, filesystem=None, cache=None):
        self.frozen = frozen or {}
        self.filesystem = filesystem or {}
        self.original_path = ['', '/lib', '.frozen']
        self.sys = types.SimpleNamespace(path=self.original_path, modules=dict(cache or {}))
        self.gc = types.SimpleNamespace(collect=Mock())
        self.imports = []
        self.failures = {}
        environment = dict(vars(builtins))
        environment['__import__'] = self.import_module
        self.ns = dict(__builtins__=environment, __name__='loader_under_test', print=Mock())
        source = (Path(__file__).resolve().parents[1] / 'firmware_loader.py').read_text()
        exec(compile(source, 'firmware_loader.py', 'exec'), self.ns)

    def import_module(self, name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'sys':
            return self.sys
        if name == 'gc':
            return self.gc
        self.imports.append((name, tuple(self.sys.path)))
        if name in self.failures:
            raise self.failures[name]
        if name in self.sys.modules:
            return self.sys.modules[name]
        for path in self.sys.path:
            table = self.frozen if path == '.frozen' else self.filesystem
            if name in table:
                self.sys.modules[name] = table[name]
                return table[name]
        raise ImportError(name)

    def load(self, name):
        return self.ns['load'](name)


def marker(build_id='frozen-source-hash'):
    return types.SimpleNamespace(BUILD_ID=build_id)


class FrozenLoaderTest(unittest.TestCase):
    def test_filesystem_marker_cannot_select_frozen_storage(self):
        harness = LoaderHarness(filesystem={'_grow_frozen_build': marker('spoof')})
        self.assertEqual(harness.ns['STORAGE'], 'filesystem')
        self.assertIsNone(harness.ns['BUILD_ID'])
        self.assertEqual(harness.imports, [('_grow_frozen_build', ('.frozen',))])
        self.assertIs(harness.sys.path, harness.original_path)

    def test_cached_filesystem_marker_is_not_trusted(self):
        spoof = marker('cached-spoof')
        harness = LoaderHarness(cache={'_grow_frozen_build': spoof})
        self.assertEqual(harness.ns['STORAGE'], 'filesystem')
        self.assertIsNone(harness.ns['BUILD_ID'])
        self.assertIs(harness.sys.modules['_grow_frozen_build'], spoof)
        self.assertIs(harness.sys.path, harness.original_path)

    def test_real_frozen_marker_replaces_cached_spoof(self):
        real = marker()
        harness = LoaderHarness(frozen={'_grow_frozen_build': real},
                                cache={'_grow_frozen_build': marker('spoof')})
        self.assertEqual(harness.ns['STORAGE'], 'frozen')
        self.assertEqual(harness.ns['BUILD_ID'], real.BUILD_ID)
        self.assertIs(harness.sys.modules['_grow_frozen_build'], real)
        self.assertIs(harness.sys.path, harness.original_path)

    def test_frozen_modules_ignore_filesystem_and_repeat_load_is_owned(self):
        real = types.ModuleType('_fw_example')
        harness = LoaderHarness(frozen={'_grow_frozen_build': marker(), '_fw_example': real},
                                filesystem={'_fw_example': types.ModuleType('stale')})
        self.assertIs(harness.load('example'), real)
        self.assertIs(harness.load('example'), real)
        self.assertIs(harness.sys.modules['example'], real)
        self.assertEqual(harness.imports.count(('_fw_example', ('.frozen',))), 1)
        self.assertIs(harness.sys.path, harness.original_path)

    def test_missing_frozen_module_never_falls_back_to_filesystem(self):
        harness = LoaderHarness(frozen={'_grow_frozen_build': marker()},
                                filesystem={'_fw_example': types.ModuleType('stale')})
        with self.assertRaises(ImportError):
            harness.load('example')
        self.assertNotIn('example', harness.sys.modules)
        self.assertIs(harness.sys.path, harness.original_path)
        self.assertEqual(harness.imports[-1], ('_fw_example', ('.frozen',)))

    def test_preloaded_foreign_public_or_compiled_module_is_rejected(self):
        for name in ('example', '_fw_example'):
            with self.subTest(name=name):
                harness = LoaderHarness(frozen={'_grow_frozen_build': marker(),
                    '_fw_example': types.ModuleType('real')}, cache={name: types.ModuleType('foreign')})
                with self.assertRaisesRegex(ImportError, 'Unverified cached'):
                    harness.load('example')
                self.assertIs(harness.sys.path, harness.original_path)

    def test_replaced_owned_cache_is_rejected(self):
        for name in ('example', '_fw_example'):
            with self.subTest(name=name):
                harness = LoaderHarness(frozen={'_grow_frozen_build': marker(),
                    '_fw_example': types.ModuleType('real')})
                harness.load('example')
                harness.sys.modules[name] = types.ModuleType('replacement')
                with self.assertRaisesRegex(ImportError, 'cache changed'):
                    harness.load('example')

    def test_path_restored_when_frozen_import_raises_non_import_error(self):
        harness = LoaderHarness(frozen={'_grow_frozen_build': marker()})
        harness.failures['_fw_example'] = MemoryError()
        with self.assertRaises(MemoryError):
            harness.load('example')
        self.assertIs(harness.sys.path, harness.original_path)
        self.assertNotIn('example', harness.sys.modules)


class UploadPackageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / 'upload.zip'
        manifest = dict(modules={}, bootstrap={}, web_pages={})
        for name in build_firmware.MODULES:
            source, bytecode = ('# ' + name).encode(), ('mpy-' + name).encode()
            (self.root / (name + '.py')).write_bytes(source)
            (self.root / ('_fw_' + name + '.mpy')).write_bytes(bytecode)
            manifest['modules'][name] = dict(source_sha256=hashlib.sha256(source).hexdigest(),
                bytecode_sha256=hashlib.sha256(bytecode).hexdigest())
        for name in ('main.py', 'firmware_loader.py'):
            data = ('# bootstrap ' + name).encode()
            (self.root / name).write_bytes(data)
            manifest['bootstrap'][name] = dict(sha256=hashlib.sha256(data).hexdigest())
        for name in ('settings.html', 'index.html', 'connected.html'):
            data = b'<html></html>'
            (self.root / name).write_bytes(data)
            manifest['web_pages'][name] = dict(sha256=hashlib.sha256(data).hexdigest())
        (self.root / 'firmware_build.json').write_text(json.dumps(manifest))
        (self.root / 'settings.json').write_text('private unit settings')
        self.patch = patch.object(build_firmware, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_complete_package_contains_memory_probe_and_bootstrap_without_unit_data(self):
        build_firmware.package_upload(self.target)
        with zipfile.ZipFile(self.target) as archive:
            names = set(archive.namelist())
            self.assertIn('_fw_memory_probe.mpy', names)
            self.assertTrue({'main.py', 'firmware_loader.py', 'firmware_build.json'} <= names)
            self.assertNotIn('settings.json', names)
            self.assertEqual(sum(name.endswith('.mpy') for name in names), len(build_firmware.MODULES))

    def test_missing_memory_probe_rejects_package(self):
        (self.root / '_fw_memory_probe.mpy').unlink()
        with self.assertRaises((OSError, RuntimeError)):
            build_firmware.package_upload(self.target)
        self.assertFalse(self.target.exists())

    def test_changed_bootstrap_rejects_package(self):
        for name in ('main.py', 'firmware_loader.py'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n# changed')
                try:
                    with self.assertRaisesRegex(RuntimeError, 'Upload file changed'):
                        build_firmware.package_upload(self.target)
                    self.assertFalse(self.target.exists())
                finally:
                    path.write_bytes(original)

    def test_incomplete_manifest_groups_reject_package(self):
        path = self.root / 'firmware_build.json'
        original = path.read_text()
        for group, name in (('modules', 'memory_probe'), ('bootstrap', 'main.py'),
                            ('bootstrap', 'firmware_loader.py'),
                            ('web_pages', 'settings.html'), ('web_pages', 'index.html'),
                            ('web_pages', 'connected.html')):
            with self.subTest(group=group, name=name):
                manifest = json.loads(original)
                del manifest[group][name]
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, 'upload manifest group: ' + group):
                    build_firmware.package_upload(self.target)
                self.assertFalse(self.target.exists())
        path.write_text(original)

    def test_unexpected_unit_data_in_manifest_is_rejected(self):
        path = self.root / 'firmware_build.json'
        manifest = json.loads(path.read_text())
        manifest['bootstrap']['settings.json'] = dict(sha256=hashlib.sha256(
            (self.root / 'settings.json').read_bytes()).hexdigest())
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'upload manifest group: bootstrap'):
            build_firmware.package_upload(self.target)
        self.assertFalse(self.target.exists())
