"""Small boot entry point; application source is in app_main.py.

Run build_firmware.py on the computer after editing firmware sources.
Upload main.py, firmware_loader.py and every generated _fw_*.mpy file.
"""
import gc

# Collect temporary import allocations before growing the split Python heap.
gc.collect()
gc.threshold(4096)

from firmware_loader import load


def run():
    print("Firmware build: precompiled-pressure-water-debug-3")
    # Load dependencies in order. These modules do not initialize sensors.
    for name in ("status_led", "credentials", "local_time", "shared_settings", "level_indicator",
                 "water_level", "volume_http", "pump_cycle", "captive_portal", "captive_http"):
        load(name)
    application = load("app_main")
    try:
        application.run()
    except Exception as e:
        print("SYSTEM ERROR:", e)
        application.status_led.set_system_error()
        raise


if __name__ == "__main__":
    run()
