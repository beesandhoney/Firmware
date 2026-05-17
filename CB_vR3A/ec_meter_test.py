from machine import Pin, ADC, TouchPad
import utime


EC_POWER_PIN = 5
EC_ADC_PIN = 35
MODE_BUTTON_PIN = 26
EC_LED_PIN = 15
WATER_LEVEL_PIN = 27

READ_INTERVAL_MS = 1000
DEBOUNCE_MS = 50
WATER_SAMPLES = 8
WATER_SAMPLE_DELAY_MS = 10

WATER_LOOKUP_TABLE = [
    (100, 0),
    (80, 25),
    (59, 50),
    (55, 100),
    (42, 250),
]


ec_power = Pin(EC_POWER_PIN, Pin.OUT)
mode_button = Pin(MODE_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
ec_led = Pin(EC_LED_PIN, Pin.OUT)

ec_adc = ADC(Pin(EC_ADC_PIN))
ec_adc.atten(ADC.ATTN_11DB)

try:
    water_touch = TouchPad(Pin(WATER_LEVEL_PIN))
except Exception as e:
    water_touch = None
    print("Failed to init water depth sensor on GPIO{}: {}".format(WATER_LEVEL_PIN, e))


def adc_to_voltage(raw):
    return raw * 3.3 / 4095


def raw_to_ec(raw):
    voltage = adc_to_voltage(raw)
    return voltage * 1013


def raw_to_depth_mm(reading):
    table = WATER_LOOKUP_TABLE
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


def read_water_raw(samples=WATER_SAMPLES, delay_ms=WATER_SAMPLE_DELAY_MS):
    if water_touch is None:
        return None

    total = 0
    for _ in range(samples):
        total += water_touch.read()
        utime.sleep_ms(delay_ms)
    return total / samples


def set_ec_power(enabled):
    ec_power.value(1 if enabled else 0)
    ec_led.value(1 if enabled else 0)
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
    print("EC + water depth interference test started")
    print("EC_POWER=GPIO{}, EC_ADC=GPIO{}, MODE_BUTTON=GPIO{}, WATER_LEVEL=GPIO{}".format(
        EC_POWER_PIN,
        EC_ADC_PIN,
        MODE_BUTTON_PIN,
        WATER_LEVEL_PIN,
    ))
    print("Press MODE button to toggle EC supply")
    print("Each line shows water depth with EC supply state, so compare OFF vs ON")

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
                    water_depth = raw_to_depth_mm(water_raw)
                    water_text = "water raw={:.1f}, depth={} mm".format(water_raw, water_depth)

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
