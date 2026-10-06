# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()

# A Ctrl-D / IDE upload performs a MicroPython soft reset, which deliberately
# leaves network interfaces active. Return both interfaces to a known state
# before main.py constructs the portal controller.
try:
    import gc
    import network
    import utime

    _boot_sta = network.WLAN(network.STA_IF)
    _boot_ap = network.WLAN(network.AP_IF)
    try:
        _boot_sta.disconnect()
    except Exception:
        pass
    _boot_sta.active(False)
    _boot_ap.active(False)
    utime.sleep_ms(200)

    del _boot_sta
    del _boot_ap
    del network
    del utime
    gc.collect()
    del gc
except Exception as _wifi_reset_error:
    print("Boot WiFi reset failed:", _wifi_reset_error)

# Keep grow-light PWM and the operation LED off until the application is ready.
try:
    import machine
    for _pin_no in (15, 18, 23):
        _pin = machine.Pin(_pin_no, machine.Pin.OUT)
        _pin.off()
    del _pin
    del _pin_no
    del machine
except Exception:
    pass
