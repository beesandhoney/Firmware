"""Run on the ESP32 to check the pressure sensor independently."""
import utime
from water_level import WATER_ADC_PIN, WaterLevelSensor, raw_to_depth_mm

sensor = WaterLevelSensor()
print("GZP6899A water level test: GPIO{}".format(WATER_ADC_PIN))
while True:
    raw = sensor.read_raw()
    print("ADC:", raw, "Depth mm:", raw_to_depth_mm(raw))
    utime.sleep_ms(500)
