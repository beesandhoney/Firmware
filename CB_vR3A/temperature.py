"""NTCG163JF103FT1H to GND, 10 kohm pull-up to 3.3 V, GPIO32.

TDK B25/50 = 3380 K, R25 = 10 kohm. Beta approximation for room temperatures.
https://product.tdk.com/en/search/sensor/ntc/chip-ntc-thermistor/info?part_no=NTCG163JF103FT1H
"""
import math

SUPPLY_UV = 3300000
PULLUP_OHMS = 10000.0
R25_OHMS = 10000.0
BETA_K = 3380.0
_adc = None


def voltage_to_celsius(voltage_uv):
    if voltage_uv is None or not 150000 < voltage_uv < 2450000:
        return None  # Reject rails/open/short and the classic ESP32 ADC's saturation region.
    resistance = PULLUP_OHMS * voltage_uv / (SUPPLY_UV - voltage_uv)
    value = 1 / (1 / 298.15 + math.log(resistance / R25_OHMS) / BETA_K) - 273.15
    return round(value, 1) if math.isfinite(value) and -40 <= value <= 125 else None


def read_celsius():
    global _adc
    from machine import ADC, Pin
    if _adc is None:
        _adc = ADC(Pin(32))
        _adc.atten(ADC.ATTN_11DB)
    # Use calibrated voltage, not a fabricated linear conversion of ADC counts.
    samples = []
    for _ in range(5):
        samples.append(_adc.read_uv())
    samples.sort()
    return voltage_to_celsius(samples[2])
