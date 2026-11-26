# Flash the working frozen ESP32 firmware

These commands install the EC calibration update built on MicroPython v1.28.0 and its matching
application files. Run them from this repository's root. Close the IDE serial
monitor first.

This image is for ESP32-WROOM / ESP32_GENERIC with 4 MB flash and a factory
application partition at `0x10000`, size `0x1f0000`. The current device was checked
and matches. Check a different device's layout before using this procedure;
these commands do not install a bootloader or partition table.

## 1. Select the port and firmware

```sh
ls /dev/cu.usb*
PORT=/dev/cu.usbserial-10
FW_DIR=.firmware-build/ec-calibration-2026-11-24/export
shasum -a 256 "$FW_DIR/frozen-micropython.bin"
```

Expected SHA-256:
`dcad12a629ead4e8796eb023f37e0843511ae888018e7e8375d2917c9ec227cc`.
Change PORT when macOS assigns a different USB serial name.
The host tools are `esptool` (the tested command uses v4.7.0) and `mpremote`.

## 2. Flash the application image

```sh
python3 -m esptool --chip esp32 --port "$PORT" --baud 115200 write_flash 0x10000 "$FW_DIR/frozen-micropython.bin"
```

Wait for `Hash of data verified`. This preserves the filesystem. Do not run
`erase_flash` or use offset `0x1000` for this application-only image.
If connection fails, hold BOOT while connecting and release it once writing starts.

## 3. Install the matching bootstrap and web files

Upload only the matched deployment files, not the whole workspace. In particular,
`diagnostics/` checkpoint JSON can fill the ESP32 filesystem.

```sh
mpremote connect "$PORT" fs cp "$FW_DIR"/deploy-frozen/*.py "$FW_DIR"/deploy-frozen/*.html "$FW_DIR"/deploy-frozen/*.css "$FW_DIR"/deploy-frozen/*.js :
mpremote connect "$PORT" fs cp -r "$FW_DIR/deploy-frozen/icons" :
```

This set contains no credentials, device settings or calibration from another unit.
The application modules are inside the firmware; `_fw_*.mpy` uploads are unnecessary.

On an existing filesystem installation, remove redundant application bytecode
after the matched files above have been uploaded. Skip this cleanup on a new device. This frees space while retaining
settings and credentials:

```sh
mpremote connect "$PORT" exec 'import os; from firmware_loader import STORAGE; assert STORAGE == "frozen"; [os.remove(n) for n in os.listdir() if n.startswith("_fw_") and n.endswith(".mpy")]'
```


## 4. Configure a unit without a water-level sensor

```sh
mpremote connect "$PORT" exec 'from firmware_loader import load; load("hardware_config").save({"version": 1, "hardware": {"water_level_sensor_installed": False}})'
```

Use that command only for a unit with no water-level sensor. Units with an
installed sensor need their own hardware configuration and water calibration.

To open Wi-Fi setup on the next boot without deleting saved credentials:

```sh
mpremote connect "$PORT" exec 'with open("wifi_portal_mode.flag", "w") as f: f.write("1")'
mpremote connect "$PORT" repl
```

Press **Ctrl-D** to reboot. Join **GrowWiFiSetup**, open
**http://192.168.4.1/**, and select a 2.4 GHz Wi-Fi network.

Confirm `FW_STORAGE frozen` and `FW_SOURCE` beginning `62de5b8e` in startup output.
Then check the web pages, lighting, sensors and pump.

Editing local Python modules requires rebuilding the frozen firmware. Uploading
edited `.py` or `_fw_*.mpy` files alone will not replace the frozen application.
