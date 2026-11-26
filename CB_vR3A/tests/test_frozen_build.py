"""Host-only fixtures validate paired source identity, layout and export guards."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pair_builder', ROOT / 'tools/build_firmware_pair.py')
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)
IMAGE_ID = 'sha256:' + 'a' * 64


def partition(address=pair.APP_OFFSET, size=pair.APP_SIZE):
    return struct.pack('<HBBII16sI', 0x50aa, 0, 0, address, size, b'factory', 0) + b'\xff' * 32


class FrozenBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        (self.root / 'build_firmware.py').write_text(
            "MODULES = ('memory_probe', 'application')\n"
            "def build_web_pages():\n"
            "    return {'settings.html': (ROOT / 'ui_templates/settings.html').read_bytes()}\n")
        for name in ('memory_probe.py', 'application.py', 'boot.py', 'main.py', 'firmware_loader.py'):
            (self.root / name).write_text('VALUE = ' + repr(name) + '\n')
        for name in ('grow.css', 'grow.js', 'wifi.js'):
            (self.root / name).write_text('test ' + name)
        for directory in ('tools', 'firmware', 'icons', 'ui_templates'):
            (self.root / directory).mkdir()
        for name in ('tools/build_firmware_pair.py', 'firmware/build_pair_in_container.sh'):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / 'icons/test.svg').write_text('<svg/>')
        (self.root / 'ui_templates/settings.html').write_text('<html>test</html>')
        self.stage = Path(self.temp.name) / 'stage'

    def prepare(self):
        return pair.stage(self.root, self.stage, IMAGE_ID)

    def fake_build(self, baseline=b'\xe9base', frozen=b'\xe9frozen'):
        manifest = self.prepare()
        for flavor, image in (('baseline', baseline), ('frozen', frozen)):
            build = self.stage / ('build-' + flavor)
            (build / 'partition_table').mkdir(parents=True)
            (build / 'micropython.bin').write_bytes(image)
            (build / 'sdkconfig').write_bytes(b'CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\n')
            (build / 'partition_table/partition-table.bin').write_bytes(partition())
        for flavor in ('filesystem', 'frozen'):
            shutil.copytree(self.stage / 'deploy-common', self.stage / ('deploy-' + flavor))
        for name in manifest['modules']:
            (self.stage / 'deploy-filesystem' / ('_fw_' + name + '.mpy')).write_bytes(b'M\x06fixture')
        provenance = {key: manifest[key] for key in ('build_id', 'image_id', 'micropython_commit')}
        provenance['idf_version'] = 'ESP-IDF v5.5.1'
        (self.stage / 'container_provenance.json').write_text(json.dumps(provenance))
        return manifest

    def test_stage_pairs_exact_sources_and_preserves_bootstrap_and_web(self):
        manifest = self.prepare()
        for name in manifest['modules']:
            self.assertEqual((self.stage / 'frozen' / ('_fw_' + name + '.py')).read_bytes(),
                             (self.root / (name + '.py')).read_bytes())
        for name in ('boot.py', 'main.py', 'firmware_loader.py'):
            self.assertEqual((self.stage / 'deploy-common' / name).read_bytes(), (self.root / name).read_bytes())
            self.assertIn(name, manifest['source_hashes'])
        self.assertEqual((self.stage / 'deploy-common/settings.html').read_text(), '<html>test</html>')
        frozen_manifest = (self.stage / 'frozen_manifest.py').read_text()
        self.assertIn('include("$(PORT_DIR)/boards/manifest.py")', frozen_manifest)
        self.assertIn('freeze("frozen")', frozen_manifest)
        self.assertNotIn('freeze(', (self.stage / 'baseline_manifest.py').read_text())
        self.assertIn(manifest['build_id'], (self.stage / 'frozen/_grow_frozen_build.py').read_text())
        self.assertFalse((self.stage / 'deploy-common/_grow_frozen_build.py').exists())

    def test_identity_is_repeatable_but_source_change_is_detected(self):
        first = self.prepare()
        second = pair.stage(self.root, Path(self.temp.name) / 'second', IMAGE_ID)
        self.assertEqual(first['build_id'], second['build_id'])
        (self.root / 'application.py').write_text('VALUE = 2\n')
        with self.assertRaisesRegex(ValueError, 'stale build'):
            pair.verify_snapshot(self.stage, self.root)

    def test_mutated_staged_source_is_rejected(self):
        self.prepare()
        (self.stage / 'frozen/_fw_application.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            pair.verify_snapshot(self.stage)

    def test_existing_stage_and_mutable_image_tag_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'immutable Docker'):
            pair.stage(self.root, self.stage, 'espressif/idf:v5.5.1')
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.prepare()

    def test_export_only_application_images_with_matching_deployment_and_hashes(self):
        manifest = self.fake_build()
        report = pair.export(self.stage, self.root)
        output = self.stage / 'export'
        self.assertEqual(report['build_id'], manifest['build_id'])
        self.assertTrue((output / 'baseline-micropython.bin').is_file())
        self.assertTrue((output / 'frozen-micropython.bin').is_file())
        self.assertFalse((output / 'firmware.bin').exists())
        self.assertFalse((output / 'partition-table.bin').exists())
        self.assertEqual((output / 'deploy-filesystem/main.py').read_bytes(),
                         (output / 'deploy-frozen/main.py').read_bytes())
        self.assertEqual(report['artifacts']['baseline']['sdkconfig_sha256'],
                         report['artifacts']['frozen']['sdkconfig_sha256'])

    def test_oversized_image_is_rejected_before_export(self):
        self.fake_build(frozen=b'\xe9' + b'x' * pair.APP_SIZE)
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            pair.export(self.stage, self.root)
        self.assertFalse((self.stage / 'export').exists())

    def test_different_sdkconfig_is_rejected(self):
        self.fake_build()
        with (self.stage / 'build-frozen/sdkconfig').open('ab') as file:
            file.write(b'CONFIG_SOMETHING_DIFFERENT=y\n')
        with self.assertRaisesRegex(ValueError, 'configuration or partition layout differs'):
            pair.export(self.stage, self.root)

    def test_wrong_partition_and_corrupted_deploy_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'existing device layout'):
            pair.validate_partition_table(partition(size=0x200000))
        self.fake_build()
        (self.stage / 'deploy-frozen/main.py').write_text('different')
        with self.assertRaisesRegex(ValueError, 'Deployment snapshot mismatch'):
            pair.export(self.stage, self.root)


if __name__ == '__main__':
    unittest.main()
