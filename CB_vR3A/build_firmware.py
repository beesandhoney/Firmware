"""Host-side build: python3 build_firmware.py --compiler /path/to/mpy-cross.

Use mpy-cross 1.28. Run after edits and upload all _fw_*.mpy with main.py and
firmware_loader.py. Existing .py sources may stay on the ESP32.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
MODULES = ('status_led', 'credentials', 'local_time', 'shared_settings', 'level_indicator',
           'water_level', 'volume_http', 'pump_cycle', 'captive_portal', 'captive_http', 'gpio', 'app_main')


def build(compiler):
    version = subprocess.check_output([compiler, '--version'], text=True).strip()
    if 'v1.28.0' not in version:
        raise RuntimeError('Expected mpy-cross v1.28.0; got ' + version)
    manifest = {'compiler': version, 'modules': {}}
    with tempfile.TemporaryDirectory() as directory:
        generated = []
        for name in MODULES:
            source = ROOT / (name + '.py')
            target_name = '_fw_' + name + '.mpy'
            temporary = Path(directory) / target_name
            # Portable bytecode only; no architecture-dependent native code.
            subprocess.run([compiler, '-o', str(temporary), '-s', source.name, str(source)], check=True)
            data = temporary.read_bytes()
            if data[:2] != b'M\x06':
                raise RuntimeError('Unexpected bytecode header for ' + name)
            manifest['modules'][name] = {
                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'bytecode_sha256': hashlib.sha256(data).hexdigest(),
                'bytes': len(data),
            }
            generated.append((target_name, data))
        # Do not replace the upload set until every source compiles.
        for name, data in generated:
            (ROOT / name).write_bytes(data)
    (ROOT / 'firmware_build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Built', len(MODULES), 'modules;', sum(m['bytes'] for m in manifest['modules'].values()), 'bytecode bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default='mpy-cross')
    build(parser.parse_args().compiler)
