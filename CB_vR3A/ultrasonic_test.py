"""Standalone HC-SR04 ultrasonic distance test for MicroPython/ESP32.

Wiring (HC-SR04 -> ESP32):
    VCC  -> 5 V
    GND  -> GND
    TRIG -> GPIO27
    ECHO -> GPIO12 through a 5 V-to-3.3 V level shifter/voltage divider

Run this file directly from Thonny or with:
    mpremote run ultrasonic_test.py

Stop the test with Ctrl-C.
"""

from machine import Pin, time_pulse_us
import time


TRIG_PIN = 27
ECHO_PIN = 12

MIN_DISTANCE_CM = 10.0
MAX_DISTANCE_CM = 50.0

# Approximate speed of sound in dry air at 20 degrees C.
SPEED_OF_SOUND_CM_PER_US = 0.0343
ECHO_TIMEOUT_US = 10_000
MEASUREMENT_INTERVAL_MS = 500


trig = Pin(TRIG_PIN, Pin.OUT, value=0)
echo = Pin(ECHO_PIN, Pin.IN)


def measure_distance_cm():
    """Return distance in cm, or None on timeout/out-of-range reading."""
    # Give the sensor a clean LOW level before the trigger pulse.
    trig.value(0)
    time.sleep_us(5)

    # The HC-SR04 requires a trigger pulse of at least 10 microseconds.
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)

    try:
        pulse_duration_us = time_pulse_us(echo, 1, ECHO_TIMEOUT_US)
    except OSError:
        # Some MicroPython ports raise OSError when the pulse times out.
        return None

    # Other MicroPython ports report a timeout as a negative value.
    if pulse_duration_us < 0:
        return None

    # The pulse covers the sound's outward and return journeys.
    distance_cm = pulse_duration_us * SPEED_OF_SOUND_CM_PER_US / 2

    if MIN_DISTANCE_CM <= distance_cm <= MAX_DISTANCE_CM:
        return distance_cm

    return None


def main():
    print("HC-SR04 ultrasonic sensor test")
    print("TRIG=GPIO{}, ECHO=GPIO{}".format(TRIG_PIN, ECHO_PIN))
    print("Valid range: {:.0f}-{:.0f} cm; press Ctrl-C to stop".format(
        MIN_DISTANCE_CM, MAX_DISTANCE_CM
    ))

    try:
        while True:
            distance = measure_distance_cm()

            if distance is None:
                print("No valid measurement within {:.0f}-{:.0f} cm".format(
                    MIN_DISTANCE_CM, MAX_DISTANCE_CM
                ))
            else:
                print("Distance to water: {:.1f} cm".format(distance))

            # HC-SR04 measurements should be separated by at least 60 ms.
            time.sleep_ms(MEASUREMENT_INTERVAL_MS)
    except KeyboardInterrupt:
        print("Ultrasonic sensor test stopped")
    finally:
        trig.value(0)


if __name__ == "__main__":
    main()
