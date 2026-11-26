"""Standalone ultrasonic reading test for the ESP32."""
import utime
from water_level import WaterLevelSensor
sensor = WaterLevelSensor()
print("Ultrasonic: TRIG GPIO27, ECHO GPIO33")
while True:
    print("Distance cm:", sensor.read_raw())
    utime.sleep_ms(500)
