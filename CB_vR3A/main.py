"""Small boot entry point; application source is in app_main.py.

Run build_firmware.py on the computer after editing firmware sources.
Upload main.py, firmware_loader.py and every generated _fw_*.mpy file.
"""
import gc

# Collect temporary import allocations before growing the split Python heap.
gc.collect()
gc.threshold(4096)

from firmware_loader import load, STORAGE, BUILD_ID


def run():
    print("Firmware build: portal-frozen-comparison-1")
    print("FW_STORAGE", STORAGE)
    if BUILD_ID:
        print("FW_SOURCE", BUILD_ID)
    load("memory_probe").report('startup-before-application')
    # Load dependencies in order. These modules do not initialize sensors.
    for name in ("status_led", "credentials", "local_time", "hardware_config", "water_config", "shared_settings", "level_indicator",
                 "water_level", "temperature", "grow_ui", "volume_http", "pump_cycle", "captive_portal", "captive_http"):
        load(name)
    hardware = load("hardware_config")
    probe = load("memory_probe")
    probe.report('before hardware_config.load')
    hardware.load()
    probe.report('after hardware_config.load')
    if hardware.installed() is True:
        probe.report('before water_config.load')
        load("water_config").load()
        probe.report('after water_config.load')
    application = load("app_main")
    try:
        application.run()
    except Exception as e:
        print("SYSTEM ERROR:", e)
        application.status_led.set_system_error()
        raise


if __name__ == "__main__":
    run()
