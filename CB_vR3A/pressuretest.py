from machine import Pin, ADC
import time

# ============================================================
# GZP6899A WATER LEVEL SENSOR TEST
# ESP32 / MicroPython
#
# Sensor signal -> GPIO33 (ADC1)
#
# Calibration measurements:
#
# Water level     ADC
# --------------------
#    0 mm          66
#   25 mm         200
#   75 mm         494
#  100 mm         625
#  150 mm         920
#  175 mm        1070
#  200 mm        1180
#
# Linear calibration:
#
#   ADC_OFFSET = 65.7
#   ADC_PER_MM = 5.59
#
#   level_mm = (ADC - ADC_OFFSET) / ADC_PER_MM
#
# ============================================================


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

ADC_PIN = 33

# Calibration constants
ADC_OFFSET = 65.7
ADC_PER_MM = 5.59

# ADC averaging
NUM_SAMPLES = 20
SAMPLE_DELAY_MS = 5

# Time between displayed measurements
MEASUREMENT_INTERVAL_MS = 500


# ------------------------------------------------------------
# ADC setup
# ------------------------------------------------------------

adc = ADC(Pin(ADC_PIN))

# 11 dB attenuation gives sufficient input range for the sensor.
adc.atten(ADC.ATTN_11DB)

# Use 12-bit ADC resolution: 0 ... 4095
adc.width(ADC.WIDTH_12BIT)


# ------------------------------------------------------------
# Read ADC
# ------------------------------------------------------------

def read_adc_average():
    """
    Read multiple ADC samples and return the average.

    Averaging reduces ADC noise while maintaining a sufficiently
    fast response for water-level measurement.
    """

    total = 0

    for _ in range(NUM_SAMPLES):
        total += adc.read()
        time.sleep_ms(SAMPLE_DELAY_MS)

    return total / NUM_SAMPLES


# ------------------------------------------------------------
# Convert ADC -> water level
# ------------------------------------------------------------

def adc_to_level_mm(raw_adc):
    """
    Convert ADC reading to water level in millimetres.

    Negative values are intentionally allowed during testing.
    This makes zero-offset errors visible.
    """

    return (raw_adc - ADC_OFFSET) / ADC_PER_MM


# ------------------------------------------------------------
# Zero calibration
# ------------------------------------------------------------

def measure_zero():
    """
    Measure the current ADC zero.

    The pressure tube must be in open air / zero differential
    pressure when this function is called.

    This function does NOT modify the calibration.
    It only reports the measured zero so that drift can be seen.
    """

    print()
    print("ZERO CHECK")
    print("----------")
    print("Tube must be in open air.")
    print("Measuring...")

    total = 0
    samples = 100

    for _ in range(samples):
        total += adc.read()
        time.sleep_ms(10)

    zero_adc = total / samples

    print("Measured zero ADC: {:.1f}".format(zero_adc))
    print("Stored zero ADC:   {:.1f}".format(ADC_OFFSET))
    print("Difference:        {:+.1f}".format(
        zero_adc - ADC_OFFSET
    ))
    print()


# ------------------------------------------------------------
# Startup information
# ------------------------------------------------------------

print()
print("================================")
print(" GZP6899A WATER LEVEL SENSOR TEST")
print("================================")
print()
print("ADC input:       GPIO{}".format(ADC_PIN))
print("ADC zero:        {:.1f}".format(ADC_OFFSET))
print("ADC/mm:          {:.3f}".format(ADC_PER_MM))
print("Samples:         {}".format(NUM_SAMPLES))
print()

print("Calibration:")
print("  ADC {:.1f} ->   0 mm".format(ADC_OFFSET))
print("  ADC {:.1f} -> 100 mm".format(
    ADC_OFFSET + 100 * ADC_PER_MM
))
print("  ADC {:.1f} -> 200 mm".format(
    ADC_OFFSET + 200 * ADC_PER_MM
))
print()

print("Starting measurements...")
print()


# ------------------------------------------------------------
# Main loop
# ------------------------------------------------------------

while True:

    # Read filtered ADC value
    raw_adc = read_adc_average()

    # Convert to water level
    level_mm = adc_to_level_mm(raw_adc)

    # Also display cm for convenience
    level_cm = level_mm / 10.0

    print(
        "ADC: {:7.1f} | "
        "Level: {:+7.1f} mm | "
        "{:+6.2f} cm".format(
            raw_adc,
            level_mm,
            level_cm
        )
    )

    time.sleep_ms(MEASUREMENT_INTERVAL_MS)