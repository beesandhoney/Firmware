# firmware/v2/tests/test_water_level.py

import utime
import gc
from water_level import WaterLevelSensor

sensor = WaterLevelSensor(pin=27)

print("Water level test started")
print("Free memory:", gc.mem_free())

while True:
    raw = sensor.read_raw()
    depth = sensor.raw_to_depth_mm(raw)

    print("Raw:", raw, "Depth mm:", depth)

    gc.collect()
    utime.sleep(1)
