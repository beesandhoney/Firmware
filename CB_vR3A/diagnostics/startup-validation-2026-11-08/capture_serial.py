"""Capture ESP32 bytes without toggling DTR/RTS; optional explicit reset command."""
import argparse
import os
import time
from pathlib import Path
import serial

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--port', default='/dev/cu.usbserial-140')
parser.add_argument('--seconds', type=int, default=90)
parser.add_argument('--reset', choices=('none', 'soft', 'machine', 'hardware'), default='none')
args = parser.parse_args()
output = Path(args.output)
if output.exists():
    raise SystemExit('Refusing to overwrite a capture')
port = serial.Serial()
port.port = args.port
port.baudrate = 115200
port.timeout = 0.2
port.dtr = False
port.rts = False
port.open()
with output.open('xb') as stream:
    os.chmod(output, 0o600)
    print('CAPTURE_READY', args.reset, flush=True)
    if args.reset == 'hardware':
        port.rts = True
        time.sleep(0.15)
        port.rts = False
    elif args.reset != 'none':
        # Opening USB serial can itself reset some development boards. Wait for
        # a confirmed friendly prompt before sending Ctrl-D or a reset command.
        deadline = time.monotonic() + 20
        pending = b''
        while time.monotonic() < deadline:
            port.write(b'\x03\x03\x02')
            data = port.read(4096)
            if data:
                stream.write(data)
                stream.flush()
                pending = (pending + data)[-8192:]
            if b'>>> ' in pending:
                break
            time.sleep(0.3)
        else:
            raise SystemExit('Could not confirm REPL; reset was not issued')
        if args.reset == 'soft':
            port.write(b'\x04')
        else:
            port.write(b'\r\nimport machine; machine.reset()\r\n')
        port.flush()
        print('RESET_ISSUED_AT_CONFIRMED_REPL', args.reset, flush=True)
    end = time.monotonic() + args.seconds
    count = 0
    while time.monotonic() < end:
        try:
            data = port.read(4096)
        except (serial.SerialException, OSError):
            print('CAPTURE_PORT_DISCONNECTED; reconnecting without reset', flush=True)
            port.close()
            while time.monotonic() < end:
                try:
                    port.open()
                    break
                except (serial.SerialException, OSError):
                    time.sleep(0.1)
            continue
        if data:
            stream.write(data)
            stream.flush()
            count += len(data)
    print('CAPTURE_SAVED', output, count, 'bytes', flush=True)
port.close()
