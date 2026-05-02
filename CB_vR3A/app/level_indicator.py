from machine import Pin
import utime

class LevelIndicator:
    def __init__(self, data_pin=4, clock_pin=14, latch_pin=22):
        self.data = Pin(data_pin, Pin.OUT, value=0)
        self.clock = Pin(clock_pin, Pin.OUT, value=0)
        self.latch = Pin(latch_pin, Pin.OUT, value=0)

        # LEDs are active-low in your schematic
        self.clear()

    def _pulse_clock(self):
        self.clock.value(1)
        utime.sleep_us(5)
        self.clock.value(0)
        utime.sleep_us(5)

    def _pulse_latch(self):
        self.latch.value(1)
        utime.sleep_us(5)
        self.latch.value(0)
        utime.sleep_us(5)

    def write_raw(self, value):
        """
        Write raw byte to 74HC595.
        1 = output HIGH
        0 = output LOW
        Since your LEDs are active-low:
        output HIGH = LED off
        output LOW  = LED on
        """
        self.latch.value(0)

        # Shift MSB first
        for bit in range(7, -1, -1):
            self.clock.value(0)
            self.data.value((value >> bit) & 1)
            utime.sleep_us(5)
            self._pulse_clock()

        self._pulse_latch()

    def clear(self):
        # All outputs HIGH = all LEDs OFF
        self.write_raw(0xFF)

    def all_on(self):
        # All outputs LOW = all LEDs ON
        self.write_raw(0x00)

    def single_led(self, index):
        """
        index 0..7
        """
        value = 0xFF              # all off
        value &= ~(1 << index)    # selected LED on
        self.write_raw(value)

    def bar(self, count):
        """
        count 0..8 LEDs on
        """
        if count < 0:
            count = 0
        if count > 8:
            count = 8

        value = 0xFF
        for i in range(count):
            value &= ~(1 << i)

        self.write_raw(value)