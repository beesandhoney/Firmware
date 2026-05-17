from machine import Pin, TouchPad
import utime

class WaterLevelSensor:
    def __init__(self, pin=27, lookup_table=None):
        self.touch = TouchPad(Pin(pin))
        self.lookup_table = lookup_table or [
            (100, 0),
            (80, 25),
            (59, 50),
            (55, 100),
            (42, 250),
        ]

    def read_raw(self, samples=8, delay_ms=10):
        total = 0
        for _ in range(samples):
            total += self.touch.read()
            utime.sleep_ms(delay_ms)
        return total / samples

    def raw_to_depth_mm(self, reading):
        table = self.lookup_table

        for i in range(len(table) - 1):
            r0, d0 = table[i]
            r1, d1 = table[i + 1]

            if r1 <= reading <= r0:
                return d0 + (reading - r0) * (d1 - d0) / (r1 - r0)

        if reading > table[0][0]:
            return table[0][1]
        if reading < table[-1][0]:
            return table[-1][1]

        return None

    def read_depth_mm(self, samples=8, delay_ms=10):
        raw = self.read_raw(samples, delay_ms)
        return self.raw_to_depth_mm(raw)
