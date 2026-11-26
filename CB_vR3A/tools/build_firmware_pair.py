#!/usr/bin/env python3
"""Stage and export matching filesystem/frozen ESP32 application builds.

The container builds both images; this tool never flashes or alters device data.
Only application binaries are exported, for the existing 0x10000/0x1f0000 slot.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MPY_COMMIT = 'e0e9fbb17ed6fd06bb76e266ae554784c9c80804'
IMAGE_REF = 'espressif/idf:v5.5.1'
APP_OFFSET = 0x10000
APP_SIZE = 0x1f0000
VFS_OFFSET = 0x200000
VFS_SIZE = 0x200000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def source_snapshot(root):
    """Read a complete coherent set without writing the workspace build outputs."""
    root = Path(root)
    spec = importlib.util.spec_from_file_location('pair_application_builder', root / 'build_firmware.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.ROOT = root
    modules = list(builder.MODULES)
    if len(set(modules)) != len(modules) or any(not re.fullmatch('[a-z_][a-z0-9_]*', n) for n in modules):
        raise ValueError('Invalid or duplicate module name')
    names = ['build_firmware.py', 'boot.py', 'main.py', 'firmware_loader.py',
             'tools/build_firmware_pair.py', 'firmware/build_pair_in_container.sh']
    names += [name + '.py' for name in modules]
    names += ['grow.css', 'grow.js', 'wifi.js']
    names += [str(p.relative_to(root)) for folder in ('ui_templates', 'icons')
              for p in sorted((root / folder).rglob('*')) if p.is_file()]
    inputs = {name: (root / name).read_bytes() for name in sorted(set(names))}
    pages = builder.build_web_pages()
    common = {name: inputs[name] for name in ('boot.py', 'main.py', 'firmware_loader.py', 'grow.css', 'grow.js', 'wifi.js')}
    common.update({name: data for name, data in inputs.items() if name.startswith('icons/')})
    common.update(pages)
    hashes = {name: digest(data) for name, data in inputs.items()}
    identity = {'modules': modules, 'source_hashes': hashes,
                'deploy_hashes': {name: digest(data) for name, data in sorted(common.items())}}
    return inputs, common, identity


def stage(project_root, target, image_id):
    target = Path(target).resolve()
    if target.exists():
        raise ValueError('Stage already exists; choose a fresh output directory')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('Use the immutable Docker image ID: sha256:<64 lowercase hex digits>')
    inputs, common, identity = source_snapshot(project_root)
    build_id = digest(canonical(identity))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pair-stage-', dir=str(target.parent)) as temp:
        temporary = Path(temp)
        files = {'source/' + name: data for name, data in inputs.items()}
        files.update({'deploy-common/' + name: data for name, data in common.items()})
        files.update({'frozen/_fw_' + name + '.py': inputs[name + '.py'] for name in identity['modules']})
        files['frozen/_grow_frozen_build.py'] = ('BUILD_ID = ' + repr(build_id) + '\n').encode()
        default = 'include("$(PORT_DIR)/boards/manifest.py")\n'
        files['baseline_manifest.py'] = default.encode()
        files['frozen_manifest.py'] = (default + 'freeze("frozen")\n').encode()
        files['build_pair_in_container.sh'] = inputs['firmware/build_pair_in_container.sh']
        files['build_firmware_pair.py'] = inputs['tools/build_firmware_pair.py']
        for name, data in files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = dict(identity, schema_version=1, build_id=build_id,
                        micropython_commit=MPY_COMMIT, image_ref=IMAGE_REF, image_id=image_id,
                        board='ESP32_GENERIC', idf_version='v5.5.1',
                        layout=dict(app_offset=APP_OFFSET, app_size=APP_SIZE,
                                    vfs_offset=VFS_OFFSET, vfs_size=VFS_SIZE),
                        snapshot_hashes={name: digest(data) for name, data in sorted(files.items())})
        (temporary / 'stage.json').write_text(json.dumps(manifest, indent=2) + '\n')
        # Reject a save during collection/generation instead of silently mixing versions.
        if source_snapshot(project_root)[2] != identity:
            raise ValueError('Sources changed while staging; retry with a stable workspace')
        temporary.rename(target)
    return manifest


def verify_snapshot(directory, project_root=None):
    directory = Path(directory)
    manifest = json.loads((directory / 'stage.json').read_text())
    for name, expected in manifest['snapshot_hashes'].items():
        path = directory / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError('Staged snapshot changed: ' + name)
    identity = {key: manifest[key] for key in ('modules', 'source_hashes', 'deploy_hashes')}
    if digest(canonical(identity)) != manifest['build_id']:
        raise ValueError('Stage source identity is inconsistent')
    if project_root is not None and source_snapshot(project_root)[2] != identity:
        raise ValueError('Workspace sources differ from staged snapshot; refusing a stale build')
    return manifest


def validate_partition_table(data):
    """Verify ESP-IDF binary partition entries without depending on esptool."""
    entries = []
    for offset in range(0, len(data) - 31, 32):
        magic, kind, subtype, address, size, label, flags = struct.unpack('<HBBII16sI', data[offset:offset + 32])
        if magic in (0xffff, 0xebeb):  # padding or MD5 record
            break
        if magic != 0x50aa:
            raise ValueError('Malformed partition table')
        entries.append(dict(type=kind, subtype=subtype, offset=address, size=size,
                            name=label.split(b'\0', 1)[0].decode('ascii')))
    apps = [row for row in entries if row['type'] == 0]
    if len(apps) != 1 or apps[0]['subtype'] != 0 or apps[0]['offset'] != APP_OFFSET or apps[0]['size'] != APP_SIZE:
        raise ValueError('Application partition does not match the existing device layout')
    for row in entries:
        end = row['offset'] + row['size']
        if end > VFS_OFFSET and not (row['type'] == 1 and row['offset'] == VFS_OFFSET and row['size'] == VFS_SIZE):
            raise ValueError('Partition overlaps the preserved filesystem range')
    return entries


def export(directory, project_root):
    directory = Path(directory)
    manifest = verify_snapshot(directory, project_root)
    provenance = json.loads((directory / 'container_provenance.json').read_text())
    for key in ('build_id', 'image_id', 'micropython_commit'):
        if provenance.get(key) != manifest[key]:
            raise ValueError('Container provenance mismatch: ' + key)
    if provenance.get('idf_version') != 'ESP-IDF v5.5.1':
        raise ValueError('Unexpected IDF version')
    output = directory / 'export'
    if output.exists():
        raise ValueError('Export already exists; retain it or use a new stage')
    artifacts, binaries, configs, partitions = {}, {}, {}, {}
    for flavor in ('baseline', 'frozen'):
        build = directory / ('build-' + flavor)
        binary = (build / 'micropython.bin').read_bytes()
        if not binary or binary[0] != 0xe9 or len(binary) > APP_SIZE:
            raise ValueError(flavor + ' application image is invalid or exceeds 0x1f0000 bytes')
        config = (build / 'sdkconfig').read_bytes()
        if b'CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y' not in config:
            raise ValueError(flavor + ' is not configured for 4 MB flash')
        table = (build / 'partition_table' / 'partition-table.bin').read_bytes()
        layout = validate_partition_table(table)
        artifacts[flavor] = dict(image_sha256=digest(binary), bytes=len(binary),
                                sdkconfig_sha256=digest(config), partition_sha256=digest(table),
                                partitions=layout)
        binaries[flavor], configs[flavor], partitions[flavor] = binary, config, table
    if configs['baseline'] != configs['frozen'] or partitions['baseline'] != partitions['frozen']:
        raise ValueError('Baseline/frozen SDK configuration or partition layout differs')
    for name in manifest['modules']:
        path = directory / 'deploy-filesystem' / ('_fw_' + name + '.mpy')
        if not path.is_file() or path.read_bytes()[:2] != b'M\x06':
            raise ValueError('Missing or invalid filesystem bytecode: ' + name)
    for flavor in ('filesystem', 'frozen'):
        for name, expected in manifest['deploy_hashes'].items():
            path = directory / ('deploy-' + flavor) / name
            if not path.is_file() or digest(path.read_bytes()) != expected:
                raise ValueError('Deployment snapshot mismatch: ' + flavor + '/' + name)
    with tempfile.TemporaryDirectory(prefix='export-', dir=str(directory)) as temp:
        temporary = Path(temp)
        for flavor, binary in binaries.items():
            (temporary / (flavor + '-micropython.bin')).write_bytes(binary)
        for flavor in ('filesystem', 'frozen'):
            shutil.copytree(directory / ('deploy-' + flavor), temporary / ('deploy-' + flavor))
        report = dict(manifest, container=provenance, artifacts=artifacts)
        report['deployment_hashes'] = {
            str(path.relative_to(temporary)): digest(path.read_bytes())
            for path in sorted(temporary.rglob('*')) if path.is_file()}
        (temporary / 'build_pair.json').write_text(json.dumps(report, indent=2) + '\n')
        temporary.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('stage')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--image-id', required=True)
    create.add_argument('--project-root', type=Path, default=ROOT)
    check = sub.add_parser('verify')
    check.add_argument('--stage', type=Path, required=True)
    check.add_argument('--project-root', type=Path)
    save = sub.add_parser('export')
    save.add_argument('--stage', type=Path, required=True)
    save.add_argument('--project-root', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == 'stage':
        result = stage(args.project_root, args.output, args.image_id)
    elif args.command == 'verify':
        result = verify_snapshot(args.stage, args.project_root)
    else:
        result = export(args.stage, args.project_root)
    print(args.command, 'OK:', result['build_id'])


if __name__ == '__main__':
    main()
