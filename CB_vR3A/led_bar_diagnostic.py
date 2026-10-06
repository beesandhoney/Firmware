"""ESP32-only LED output test. Stop main.py before running this file.

At the MicroPython REPL:
    exec(open('led_bar_diagnostic.py').read())

Uses the same compiled driver as the application. Alternates all off/on every
500 ms for eight seconds, then clears the bar. No sensor or calibration is used.
Printed commands are not electrical feedback from the shift register.
"""
import utime
from firmware_loader import load


def run():
    driver = load('level_indicator')
    leds = driver.LevelIndicator(data_pin=4, clock_pin=14, latch_pin=22)
    print('LED TEST: DATA=4 CLOCK=14 LATCH=22; expect OFF/ON every 500 ms')
    try:
        for step in range(16):
            count = 8 if step % 2 else 0
            started = utime.ticks_us()
            leds.bar(count)
            write_us = utime.ticks_diff(utime.ticks_us(), started)
            print('LED TEST: t={} ms command={}/8 write={} us'.format(
                utime.ticks_ms(), count, write_us))
            utime.sleep_ms(500)
    finally:
        leds.clear()
        print('LED TEST: finished; bar cleared. Reset device to resume application.')


run()
