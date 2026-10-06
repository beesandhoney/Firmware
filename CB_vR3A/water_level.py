"""GZP6899A pressure sensor on ESP32 ADC1, GPIO33."""
from machine import Pin, ADC
import utime

WATER_ADC_PIN = 33
ADC_OFFSET = 65.7
ADC_PER_MM = 5.59


def raw_to_depth_mm(raw):
    if raw is None:
        return None
    return round(max(0.0, (raw - ADC_OFFSET) / ADC_PER_MM), 2)


class WaterLevelSensor:
    def __init__(self, adc_pin=WATER_ADC_PIN):
        self.adc = ADC(Pin(adc_pin))
        self.adc.atten(ADC.ATTN_11DB)
        self.adc.width(ADC.WIDTH_12BIT)

    def read_raw(self, samples=20, delay_ms=5):
        samples = max(1, int(samples))
        total = 0
        try:
            for index in range(samples):
                total += self.adc.read()
                if index + 1 < samples:
                    utime.sleep_ms(delay_ms)
        except OSError:
            return None
        return total / samples
