"""Host-side build: python3 build_firmware.py --compiler /path/to/mpy-cross.

Use mpy-cross 1.28. Run after edits and upload all _fw_*.mpy with main.py and
firmware_loader.py. Existing .py sources may stay on the ESP32.
"""
import base64
import re
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
MODULES = ('memory_probe', 'status_led', 'credentials', 'local_time', 'hardware_config', 'water_config', 'shared_settings', 'level_indicator',
           'water_level', 'temperature', 'grow_ui', 'volume_http', 'pump_cycle', 'captive_portal', 'captive_http', 'gpio', 'app_main')
BOOTSTRAP_FILES = ('main.py', 'firmware_loader.py')
WEB_PAGE_FILES = ('settings.html', 'index.html', 'connected.html')


def build_web_pages():
    """Bundle shared source assets on the host; devices stream one page from flash."""
    css = (ROOT / 'grow.css').read_text()
    icons = {p.name: 'data:image/svg+xml;base64,' + base64.b64encode(p.read_bytes()).decode()
             for p in (ROOT / 'icons').glob('*.svg')}
    scripts = {name: (ROOT / name).read_text() for name in ('grow.js', 'wifi.js')}
    revision = hashlib.sha256((css + ''.join(scripts.values()) + json.dumps(icons, sort_keys=True)).encode()).hexdigest()[:12]
    pages = {}
    for name in WEB_PAGE_FILES:
        page = (ROOT / 'ui_templates' / name).read_text()
        page = page.replace('<link rel="stylesheet" href="/grow.css">', '<style data-grow-style="shared">' + css + '</style>')
        for script, source in scripts.items():
            inline = ('const GROW_ICON_URLS = ' + json.dumps(icons) + ';\n' if script == 'grow.js' else '') + source
            page = page.replace('<script src="/' + script + '" defer></script>', '<script>' + inline + '</script>')
        for icon, url in icons.items():
            page = page.replace('src="/icons/' + icon + '"', 'src="' + url + '"')
        page = page.replace('</head>', '<meta name="grow-ui-build" content="' + revision + '"></head>')
        if re.search(r'(?:src|href)="/(?:grow\.(?:css|js)|wifi\.js|icons/)', page):
            raise RuntimeError('Unbundled UI asset in ' + name)
        pages[name] = page.encode()
    return pages


def build(compiler):
    version = subprocess.check_output([compiler, '--version'], text=True).strip()
    if 'v1.28.0' not in version:
        raise RuntimeError('Expected mpy-cross v1.28.0; got ' + version)
    pages = build_web_pages()
    manifest = {'compiler': version, 'modules': {}, 'web_pages': {}, 'bootstrap': {}}
    for name in BOOTSTRAP_FILES:
        manifest['bootstrap'][name] = {'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
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
    for name, data in pages.items():
        (ROOT / name).write_bytes(data)
        manifest['web_pages'][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    (ROOT / 'firmware_build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Built', len(MODULES), 'modules;', sum(m['bytes'] for m in manifest['modules'].values()), 'bytecode bytes')


def package_upload(target):
    """Create a verified complete application upload; never include unit data."""
    manifest = json.loads((ROOT / 'firmware_build.json').read_text())
    for group, required in (('modules', MODULES), ('bootstrap', BOOTSTRAP_FILES),
                            ('web_pages', WEB_PAGE_FILES)):
        entries = manifest.get(group)
        if not isinstance(entries, dict) or set(entries) != set(required):
            raise RuntimeError('Incomplete or unexpected upload manifest group: ' + group)
    files = {}
    for name in MODULES:
        entry = manifest['modules'][name]
        if hashlib.sha256((ROOT / (name + '.py')).read_bytes()).hexdigest() != entry['source_sha256']:
            raise RuntimeError('Source changed since build: ' + name)
        filename = '_fw_' + name + '.mpy'
        data = (ROOT / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['bytecode_sha256']:
            raise RuntimeError('Bytecode changed since build: ' + filename)
        files[filename] = data
    for group in ('bootstrap', 'web_pages'):
        for name, entry in manifest[group].items():
            data = (ROOT / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise RuntimeError('Upload file changed since build: ' + name)
            files[name] = data
    files['firmware_build.json'] = (ROOT / 'firmware_build.json').read_bytes()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    temporary.replace(target)
    print('Verified application upload package:', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default='mpy-cross')
    parser.add_argument('--package', help='Also write a verified complete application upload ZIP')
    args = parser.parse_args()
    build(args.compiler)
    if args.package:
        package_upload(args.package)
