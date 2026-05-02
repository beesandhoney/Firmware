from machine import Pin, ADC
import utime

# --------------------
# Pin map - new hardware
# --------------------
EC_LED_PIN = 15          # EC indicator LED
EC_ADC_PIN = 35          # EC op-amp input, ADC input only
EC_POWER_PIN = 5         # EC meter power enable

TEMP_ADC_PIN = 32        # Temperature analog input
MODE_BUTTON_PIN = 26     # Mode button
PHOTO_ADC_PIN = 34       # Photo NPN / light input, ADC input only
FAN_PIN = 25             # Fan output
ON_OFF_DIM_BUTTON_PIN = 17
PUMP1_PIN = 13           # Pump 1 output

# --------------------
# Outputs
# --------------------
ec_led = Pin(EC_LED_PIN, Pin.OUT)
ec_power = Pin(EC_POWER_PIN, Pin.OUT)
fan = Pin(FAN_PIN, Pin.OUT)
pump1 = Pin(PUMP1_PIN, Pin.OUT)

# --------------------
# Inputs
# GPIO34 and GPIO35 are input-only and have no internal pullups.
# --------------------
mode_button = Pin(MODE_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
on_off_dim_button = Pin(ON_OFF_DIM_BUTTON_PIN, Pin.IN, Pin.PULL_UP)

ec_adc = ADC(Pin(EC_ADC_PIN))
ec_adc.atten(ADC.ATTN_11DB)

temp_adc = ADC(Pin(TEMP_ADC_PIN))
temp_adc.atten(ADC.ATTN_11DB)

photo_adc = ADC(Pin(PHOTO_ADC_PIN))
photo_adc.atten(ADC.ATTN_11DB)


def adc_to_voltage(raw):
    return raw * 3.3 / 4095


def read_ec_value():
    """
    Placeholder EC conversion.
    Replace this with your calibrated formula later.
    """
    raw = ec_adc.read()
    voltage = adc_to_voltage(raw)

    # Temporary rough conversion only
    ec_value = voltage * 1013

    return raw, voltage, ec_value


def blink_ec_led_for_ec(ec_value):
    """
    Slow blink below 500 EC.
    Fast blink above 2000 EC.
    Solid ON between 500 and 2000 EC.
    """
    if ec_value < 500:
        ec_led.on()
        utime.sleep_ms(700)
        ec_led.off()
        utime.sleep_ms(700)

    elif ec_value > 2000:
        ec_led.on()
        utime.sleep_ms(120)
        ec_led.off()
        utime.sleep_ms(120)

    else:
        ec_led.on()
        utime.sleep_ms(300)


def test_outputs_once():
    print("Testing outputs...")

    print("EC LED ON")
    ec_led.on()
    utime.sleep(1)
    ec_led.off()

    print("EC power ON")
    ec_power.on()
    utime.sleep(1)
    ec_power.off()

    print("Fan ON")
    fan.on()
    utime.sleep(1)
    fan.off()

    print("Pump 1 ON")
    pump1.on()
    utime.sleep(1)
    pump1.off()

    print("Output test complete")


def print_inputs():
    ec_raw, ec_voltage, ec_value = read_ec_value()
    temp_raw = temp_adc.read()
    photo_raw = photo_adc.read()

    print(
        "MODE={}, ON_OFF_DIM={}, EC raw={}, EC V={:.2f}, EC={:.0f}, TEMP raw={}, PHOTO raw={}".format(
            mode_button.value(),
            on_off_dim_button.value(),
            ec_raw,
            ec_voltage,
            ec_value,
            temp_raw,
            photo_raw,
        )
    )


def main():
    print("Starting hardware test")
    print("Buttons are assumed active-low: 1=released, 0=pressed")
    print("GPIO34/GPIO35 are ADC input-only pins")
    print("Press MODE button to toggle fan")
    print("Press ON_OFF_DIM button to toggle pump")
    print("EC LED blink speed follows EC value")

    test_outputs_once()

    ec_power.on()
    print("EC meter power enabled")

    fan_state = 0
    pump_state = 0

    last_mode = 1
    last_dim = 1

    last_print_ms = utime.ticks_ms()

    while True:
        mode_now = mode_button.value()
        dim_now = on_off_dim_button.value()

        # Falling edge = button press
        if last_mode == 1 and mode_now == 0:
            fan_state = not fan_state
            fan.value(fan_state)
            print("Fan toggled:", fan_state)
            utime.sleep_ms(200)

        if last_dim == 1 and dim_now == 0:
            pump_state = not pump_state
            pump1.value(pump_state)
            print("Pump toggled:", pump_state)
            utime.sleep_ms(200)

        last_mode = mode_now
        last_dim = dim_now

        ec_raw, ec_voltage, ec_value = read_ec_value()
        blink_ec_led_for_ec(ec_value)

        now = utime.ticks_ms()
        if utime.ticks_diff(now, last_print_ms) > 1000:
            print_inputs()
            last_print_ms = now


try:
    main()
except KeyboardInterrupt:
    print("Stopping test")
finally:
    ec_led.off()
    ec_power.off()
    fan.off()
    pump1.off()