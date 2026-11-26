"""Ultrasonic water distance in cm: TRIG GPIO27, ECHO GPIO33."""
from machine import Pin, time_pulse_us
import utime

WATER_TRIG_PIN = 27
WATER_ECHO_PIN = 33


class WaterLevelSensor:
    def __init__(self, trig_pin=WATER_TRIG_PIN, echo_pin=WATER_ECHO_PIN):
        self.trig = Pin(trig_pin, Pin.OUT, value=0)
        self.echo = Pin(echo_pin, Pin.IN)
        self.last_trigger_ms = None

    def read_once_cm(self):
        if self.last_trigger_ms is not None:
            remaining = 60 - utime.ticks_diff(utime.ticks_ms(), self.last_trigger_ms)
            if remaining > 0:
                utime.sleep_ms(remaining)
        self.trig.value(0)
        utime.sleep_us(5)
        self.last_trigger_ms = utime.ticks_ms()
        self.trig.value(1)
        utime.sleep_us(10)
        self.trig.value(0)
        try:
            pulse = time_pulse_us(self.echo, 1, 30000)
        except OSError:
            return None
        distance = pulse * 0.0343 / 2
        return distance if 2 <= distance <= 400 else None

    def read_raw(self, samples=3, delay_ms=60):
        readings = []
        for index in range(max(1, int(samples))):
            if index:
                utime.sleep_ms(max(60, int(delay_ms)))
            distance = self.read_once_cm()
            if distance is not None:
                readings.append(distance)
        if not readings:
            return None
        readings.sort()
        middle = len(readings) // 2
        return (readings[middle] if len(readings) % 2 else
                (readings[middle - 1] + readings[middle]) / 2)

    def read_distance_cm(self, samples=3, delay_ms=60):
        return self.read_raw(samples, delay_ms)
