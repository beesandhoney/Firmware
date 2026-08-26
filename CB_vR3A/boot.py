# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()

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
