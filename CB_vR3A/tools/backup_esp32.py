"""Read-only, chunked ESP32 recovery backup using esptool's read digest checks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def backup(port, directory, size=0x400000, chunk_size=0x40000):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    output = directory / 'flash.bin'
    if output.exists():
        raise RuntimeError('Refusing to overwrite an existing recovery image')
    chunks = []
    for offset in range(0, size, chunk_size):
        length = min(chunk_size, size - offset)
        part = directory / ('flash-%06x.bin' % offset)
        command = [sys.executable, '-m', 'esptool', '--chip', 'esp32', '--port', port,
                   '--baud', '115200', '--after', 'no_reset', 'read_flash', '--no-progress',
                   hex(offset), hex(length), str(part)]
        for attempt in range(1, 4):
            with (directory / ('read-%06x-%d.log' % (offset, attempt))).open('wb') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode == 0 and part.exists() and part.stat().st_size == length:
                break
            print('Retry read at', hex(offset), 'attempt', attempt, flush=True)
        else:
            raise RuntimeError('Backup incomplete at ' + hex(offset))
        part.chmod(0o600)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        chunks.append(dict(offset=offset, bytes=length, sha256=digest, file=part.name))
        print('Backed up', offset + length, '/', size, flush=True)
    with output.open('xb') as target:
        for chunk in chunks:
            data = (directory / chunk['file']).read_bytes()
            if hashlib.sha256(data).hexdigest() != chunk['sha256']:
                raise RuntimeError('Chunk changed during backup')
            target.write(data)
    output.chmod(0o600)
    manifest = dict(bytes=output.stat().st_size, sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    read_verification='esptool stub MD5 per read, host SHA256 per saved chunk', chunks=chunks)
    (directory / 'backup.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Complete recovery image:', output, 'SHA256:', manifest['sha256'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', type=lambda value: int(value, 0), default=0x400000)
    args = parser.parse_args()
    if args.size <= 0 or args.size % 4096:
        parser.error('size must be a positive multiple of 4096')
    backup(args.port, args.output, args.size)
