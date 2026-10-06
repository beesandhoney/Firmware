from machine import Pin, ADC
import utime
from water_level import WATER_ECHO_PIN, WATER_TRIG_PIN, WaterLevelSensor


EC_POWER_PIN = 5
EC_ADC_PIN = 35
MODE_BUTTON_PIN = 26
READ_INTERVAL_MS = 1000
DEBOUNCE_MS = 50
WATER_SAMPLES = 3
WATER_SAMPLE_DELAY_MS = 60


ec_power = Pin(EC_POWER_PIN, Pin.OUT)
mode_button = Pin(MODE_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
ec_adc = ADC(Pin(EC_ADC_PIN))
ec_adc.atten(ADC.ATTN_11DB)

try:
    water_sensor = WaterLevelSensor(WATER_TRIG_PIN, WATER_ECHO_PIN)
except Exception as e:
    water_sensor = None
    print("Failed to init ultrasonic sensor: {}".format(e))


def adc_to_voltage(raw):
    return raw * 3.3 / 4095


def raw_to_ec(raw):
    voltage = adc_to_voltage(raw)
    return voltage * 1013


def read_water_raw(samples=WATER_SAMPLES, delay_ms=WATER_SAMPLE_DELAY_MS):
    if water_sensor is None:
        return None
    return water_sensor.read_distance_cm(samples, delay_ms)


def set_ec_power(enabled):
    ec_power.value(1 if enabled else 0)
    print("EC supply GPIO{} {}".format(EC_POWER_PIN, "ON" if enabled else "OFF"))


def button_pressed(last_level, last_change_ms):
    now = utime.ticks_ms()
    level = mode_button.value()

    if level != last_level:
        return False, level, now

    if level == 0 and utime.ticks_diff(now, last_change_ms) >= DEBOUNCE_MS:
        while mode_button.value() == 0:
            utime.sleep_ms(10)
        return True, 1, utime.ticks_ms()

    return False, last_level, last_change_ms


def main():
    print("EC + ultrasonic water distance interference test started")
    print("EC_POWER=GPIO{}, EC_ADC=GPIO{}, MODE_BUTTON=GPIO{}, WATER_TRIG=GPIO{}, WATER_ECHO=GPIO{}".format(
        EC_POWER_PIN,
        EC_ADC_PIN,
        MODE_BUTTON_PIN,
        WATER_TRIG_PIN,
        WATER_ECHO_PIN,
    ))
    print("Press MODE button to toggle EC supply")
    print("Each line shows ultrasonic distance with EC supply state, so compare OFF vs ON")

    ec_enabled = False
    set_ec_power(ec_enabled)

    last_button_level = mode_button.value()
    last_button_change_ms = utime.ticks_ms()
    last_read_ms = utime.ticks_ms() - READ_INTERVAL_MS

    try:
        while True:
            pressed, last_button_level, last_button_change_ms = button_pressed(
                last_button_level,
                last_button_change_ms,
            )
            if pressed:
                ec_enabled = not ec_enabled
                set_ec_power(ec_enabled)
                if ec_enabled:
                    print("Waiting 200ms for EC circuit to settle")
                    utime.sleep_ms(200)

            now = utime.ticks_ms()
            if utime.ticks_diff(now, last_read_ms) >= READ_INTERVAL_MS:
                last_read_ms = now
                water_raw = read_water_raw()
                if water_raw is None:
                    water_text = "water unavailable"
                else:
                    water_text = "water distance={:.2f} cm".format(water_raw)

                if ec_enabled:
                    raw = ec_adc.read()
                    voltage = adc_to_voltage(raw)
                    ec_value = raw_to_ec(raw)
                    print("EC=ON, EC raw={}, voltage={:.3f}V, EC={:.1f} uS/cm, {}".format(
                        raw,
                        voltage,
                        ec_value,
                        water_text,
                    ))
                else:
                    print("EC=OFF, {}".format(water_text))

            utime.sleep_ms(20)
    finally:
        set_ec_power(False)
        print("EC meter test stopped")


main()
