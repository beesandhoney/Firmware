#!/usr/bin/env bash
set -euo pipefail

# Run the immutable staged copy in espressif/idf:v5.5.1 by its recorded image ID.
# /project is the repository mount. This script builds; it never flashes.
pair_dir="${1:?Usage: bash build_pair_in_container.sh /project/.firmware-build/pair-name}"
project_dir=/project
mpy_dir="$project_dir/.firmware-build/micropython"
jobs="${FIRMWARE_BUILD_JOBS:-4}"
expected_commit=e0e9fbb17ed6fd06bb76e266ae554784c9c80804
git config --global --add safe.directory "$mpy_dir"
test "$(git -C "$mpy_dir" rev-parse HEAD)" = "$expected_commit"
git -C "$mpy_dir" diff --quiet HEAD --
test "$(idf.py --version)" = 'ESP-IDF v5.5.1'
python3 "$pair_dir/build_firmware_pair.py" verify --stage "$pair_dir" --project-root "$project_dir"
test ! -e "$pair_dir/build-baseline"
test ! -e "$pair_dir/build-frozen"
mkdir -p "$pair_dir/logs"

make -C "$mpy_dir/mpy-cross" -j"$jobs" 2>&1 | tee "$pair_dir/logs/mpy-cross.log"
# Prevent CMake's recursive make from inheriting the ESP32 BUILD override and
# writing host compiler objects into the target firmware build directory.
export MICROPY_MPYCROSS="$mpy_dir/mpy-cross/build/mpy-cross"
make -C "$mpy_dir/ports/esp32" BOARD=ESP32_GENERIC BUILD="$pair_dir/build-submodules" submodules \
    2>&1 | tee "$pair_dir/logs/submodules.log"

# IDF's component manager may reformat the pinned YAML lockfile. Preserve the
# generated copy for provenance, but keep the checkout/version clean only when
# the parsed dependency content is exactly unchanged.
python3 - "$mpy_dir" "$pair_dir" <<'PYLOCK'
import pathlib, subprocess, sys, yaml
mpy, stage = map(pathlib.Path, sys.argv[1:])
name = 'ports/esp32/lockfiles/dependencies.lock.esp32'
original = subprocess.check_output(['git', '-C', str(mpy), 'show', 'HEAD:' + name])
path = mpy / name
generated = path.read_bytes()
if yaml.safe_load(original) != yaml.safe_load(generated):
    raise SystemExit('Component dependencies changed; refusing an unpinned build')
(stage / 'generated-component-lockfile.yaml').write_bytes(generated)
path.write_bytes(original)
PYLOCK

python3 - "$pair_dir" "$mpy_dir" <<'PY'
import json, pathlib, shutil, subprocess, sys
stage, mpy = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((stage / 'stage.json').read_text())
for flavor in ('filesystem', 'frozen'):
    shutil.copytree(stage / 'deploy-common', stage / ('deploy-' + flavor))
compiler = mpy / 'mpy-cross/build/mpy-cross'
version = subprocess.check_output([str(compiler), '--version'], text=True).strip()
if 'v1.28.0' not in version:
    raise SystemExit('Unexpected mpy-cross version: ' + version)
for name in manifest['modules']:
    source_name = '_fw_' + name + '.py'
    subprocess.run([str(compiler), '-s', source_name, '-o',
                    str(stage / 'deploy-filesystem' / ('_fw_' + name + '.mpy')),
                    str(stage / 'frozen' / source_name)], check=True)
(stage / 'mpy-cross-version.txt').write_text(version + '\n')
PY

for flavor in baseline frozen; do
    make -C "$mpy_dir/ports/esp32" BOARD=ESP32_GENERIC \
        BUILD="$pair_dir/build-$flavor" FROZEN_MANIFEST="$pair_dir/${flavor}_manifest.py" \
        2>&1 | tee "$pair_dir/logs/$flavor.log"
done

python3 - "$pair_dir" "$mpy_dir" <<'PY'
import json, os, pathlib, subprocess, sys
stage, mpy = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((stage / 'stage.json').read_text())
def command(*args):
    return subprocess.check_output(args, text=True).strip()
record = dict(build_id=manifest['build_id'], image_ref=manifest['image_ref'],
              image_id=manifest['image_id'],
              micropython_commit=command('git', '-C', str(mpy), 'rev-parse', 'HEAD'),
              idf_version=command('idf.py', '--version'),
              idf_commit=command('git', '-C', os.environ['IDF_PATH'], 'rev-parse', 'HEAD'),
              mpy_cross=(stage / 'mpy-cross-version.txt').read_text().strip(),
              submodules=command('git', '-C', str(mpy), 'submodule', 'status', '--recursive'))
(stage / 'container_provenance.json').write_text(json.dumps(record, indent=2) + '\n')
PY
python3 "$pair_dir/build_firmware_pair.py" verify --stage "$pair_dir" --project-root "$project_dir"
python3 "$pair_dir/build_firmware_pair.py" export --stage "$pair_dir" --project-root "$project_dir"
