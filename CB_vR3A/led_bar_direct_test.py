"""Run at the ESP32 REPL after interrupting main.py with Ctrl+C.

exec(open('led_bar_direct_test.py').read())

Bypasses firmware_loader, all _fw_ files, and level_indicator imports.
Writes OFF twice, ON twice, then OFF twice with four-second holds.
Record the actual LED state at each numbered step; final cleanup commands OFF.
"""
from machine import Pin
import utime


def run_direct_test():
    data = Pin(4, Pin.OUT, value=0)
    clock = Pin(14, Pin.OUT, value=0)
    latch = Pin(22, Pin.OUT, value=0)

    def write_byte(value):
        latch.value(0)
        clock.value(0)
        utime.sleep_us(100)
        for bit in range(7, -1, -1):
            data.value((value >> bit) & 1)
            utime.sleep_us(100)
            clock.value(1)
            utime.sleep_us(100)
            clock.value(0)
            utime.sleep_us(100)
        latch.value(1)
        utime.sleep_us(100)
        latch.value(0)

    print('DIRECT LED TEST v2: GPIO4 DATA, GPIO14 CLOCK, GPIO22 LATCH')
    try:
        sequence = ((0xFF, 'OFF 1'), (0xFF, 'OFF 2'),
                    (0x00, 'ON 1'), (0x00, 'ON 2'),
                    (0xFF, 'OFF 3'), (0xFF, 'OFF 4'))
        for step, (value, label) in enumerate(sequence):
            write_byte(value)
            print('DIRECT LED TEST: step {} commanded {} byte=0x{:02X}'.format(
                step + 1, label, value))
            utime.sleep_ms(4000)
    finally:
        write_byte(0xFF)
        print('DIRECT LED TEST: finished; commanded OFF. Reset to resume firmware.')


run_direct_test()
