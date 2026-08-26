from machine import Pin, PWM, ADC, TouchPad
import utime
from shared_settings import WaterCalibration, raw_to_liters
from level_indicator import LevelIndicator


EC_ADC_PIN = 35
EC_POWER_PIN = 5
TEMP_ADC_PIN = 32
MODE_BUTTON_PIN = 26
PHOTO_ADC_PIN = 34
FAN_PIN = 25
ON_OFF_DIM_BUTTON_PIN = 17
PUMP1_PIN = 13
WATER_LEVEL_PIN = 27
PWM_LED_1_PIN = 18
PWM_LED_2_PIN = 23
LEVEL_DATA_PIN = 4
LEVEL_CLOCK_PIN = 14
LEVEL_LATCH_PIN = 22

EC_LOW_THRESHOLD = 300
EC_HIGH_THRESHOLD = 2000
FAN_ON_PERCENT = 50

# (raw ADC count, reference conductivity in uS/cm).
# GPIO voltages measured at these points were 340, 620, 940, 1630, 2247,
# and 2325 mV respectively.
EC_CALIBRATION_POINTS = (
    (256, 300.0),
    (598, 580.0),
    (980, 820.0),
    (1840, 1368.0),
    (2600, 2170.0),
    (2699, 2720.0),
)

def setup_adc():
    adc = ADC(Pin(EC_ADC_PIN))
    adc.atten(ADC.ATTN_11DB)  # Set the attenuation for reading up to 3.3V
    return adc


def setup_ambient_adc():
    adc = ADC(Pin(PHOTO_ADC_PIN))
    adc.atten(ADC.ATTN_11DB)
    return adc


def read_ambient_raw(adc):
    try:
        return adc.read()
    except Exception as e:
        print("Failed to read ambient light ADC:", e)
        return None

def raw_to_ec(raw):
    """Convert an EC ADC count to uS/cm using piecewise linear calibration."""
    points = EC_CALIBRATION_POINTS

    # Extrapolate below the measured range using the first segment, but never
    # report a physically impossible negative conductivity.
    if raw <= points[0][0]:
        raw0, ec0 = points[0]
        raw1, ec1 = points[1]
        value = ec0 + (raw - raw0) * (ec1 - ec0) / (raw1 - raw0)
        return max(0.0, value)

    for index in range(len(points) - 1):
        raw0, ec0 = points[index]
        raw1, ec1 = points[index + 1]
        if raw <= raw1:
            return ec0 + (raw - raw0) * (ec1 - ec0) / (raw1 - raw0)

    # The analog output saturates above the final measured point, so values
    # beyond it cannot be distinguished reliably.
    return points[-1][1]


def read_ec_value(adc):
    raw = adc.read()
    ec_value = raw_to_ec(raw)
    print("EC raw ADC: {}, calibrated: {:.1f} uS/cm".format(raw, ec_value))
    return ec_value

ec_power_pin = Pin(EC_POWER_PIN, Pin.OUT)
ec_power_pin.off()  # Keep EC inactive except during an explicitly scheduled reading.
fan_pin = Pin(FAN_PIN, Pin.OUT)
mode_button_pin = Pin(MODE_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
on_off_dim_button_pin = Pin(ON_OFF_DIM_BUTTON_PIN, Pin.IN, Pin.PULL_UP)

try:
    level_indicator = LevelIndicator(
        data_pin=LEVEL_DATA_PIN,
        clock_pin=LEVEL_CLOCK_PIN,
        latch_pin=LEVEL_LATCH_PIN,
    )
except Exception as e:
    level_indicator = None
    print("Failed to init level indicator:", e)


def set_ec_power(enabled):
    """Enable the EC meter only for its mutually exclusive measurement window."""
    ec_power_pin.value(1 if enabled else 0)


def update_water_level_indicator(volume_l):
    if level_indicator is None:
        return
    if volume_l is None:
        level_indicator.clear()
        return
    try:
        count = int((float(volume_l) * 8) / WaterCalibration.max_volume_l)
        if volume_l > 0 and count < 1:
            count = 1
        level_indicator.bar(count)
    except Exception as e:
        print("Failed to update water level indicator:", e)


class LongPressDetector:
    """
    Release-based duration detector with explicit press bands.
    0 = pressed, 1 = released (active-low button with pull-up).
    """

    def __init__(
        self,
        button_pin,
        short_press_callback=None,
        long_press_callback=None,
        settings_press_callback=None,
        wifi_portal_callback=None,
        wifi_reset_callback=None,
        debounce_ms=50,
        short_press_min=50,
        short_press_max=1500,
        settings_press_min=2000,
        settings_press_max=5000,
        wifi_portal_min=5000,
        wifi_portal_max=10000,
        wifi_reset_min=10000,
    ):
        self.sw = button_pin
        self.short_press_callback = short_press_callback
        self.long_press_callback = long_press_callback  # backward compatibility
        self.settings_press_callback = settings_press_callback
        self.wifi_portal_callback = wifi_portal_callback
        self.wifi_reset_callback = wifi_reset_callback

        self.debounce_ms = debounce_ms
        self.short_press_min = short_press_min
        self.short_press_max = short_press_max
        self.settings_press_min = settings_press_min
        self.settings_press_max = settings_press_max
        self.wifi_portal_min = wifi_portal_min
        self.wifi_portal_max = wifi_portal_max
        self.wifi_reset_min = wifi_reset_min

        self._raw_level = self.sw.value()
        self._stable_level = self._raw_level
        self._last_raw_change_ms = utime.ticks_ms()
        self._press_start_ms = None

    def _handle_release(self, elapsed_ms):
        if self.wifi_reset_callback and elapsed_ms >= self.wifi_reset_min:
            self.wifi_reset_callback()
            return

        if self.wifi_portal_callback and self.wifi_portal_min <= elapsed_ms < self.wifi_portal_max:
            self.wifi_portal_callback()
            return

        if self.settings_press_callback and self.settings_press_min <= elapsed_ms < self.settings_press_max:
            self.settings_press_callback()
            return

        if self.short_press_callback and self.short_press_min <= elapsed_ms <= self.short_press_max:
            self.short_press_callback()
            return

        if self.long_press_callback and elapsed_ms >= self.settings_press_min:
            self.long_press_callback()

    def update(self):
        now = utime.ticks_ms()
        raw = self.sw.value()

        if raw != self._raw_level:
            self._raw_level = raw
            self._last_raw_change_ms = now
            return

        if raw != self._stable_level and utime.ticks_diff(now, self._last_raw_change_ms) >= self.debounce_ms:
            self._stable_level = raw
            if self._stable_level == 0:
                self._press_start_ms = now
            else:
                if self._press_start_ms is not None:
                    elapsed = utime.ticks_diff(now, self._press_start_ms)
                    self._press_start_ms = None
                    self._handle_release(elapsed)

last_value = None  # Initialize the last_value outside the function

pwm_led_1_pin = Pin(PWM_LED_1_PIN, Pin.OUT)
pwm_led_2_pin = Pin(PWM_LED_2_PIN, Pin.OUT)
pwm_led_1_pin.off()
pwm_led_2_pin.off()
pwm_led_1 = None
pwm_led_2 = None


def _ensure_led_pwm():
    global pwm_led_1, pwm_led_2
    if pwm_led_1 is None:
        pwm_led_1 = PWM(pwm_led_1_pin, freq=5000)
    if pwm_led_2 is None:
        pwm_led_2 = PWM(pwm_led_2_pin, freq=5000)


def led_pwm_off():
    global last_value, pwm_led_1, pwm_led_2
    for pwm in (pwm_led_1, pwm_led_2):
        if pwm is not None:
            try:
                pwm.duty(0)
                pwm.deinit()
            except Exception:
                pass
    pwm_led_1 = None
    pwm_led_2 = None
    pwm_led_1_pin.off()
    pwm_led_2_pin.off()
    last_value = 0


def brightnessControl(duty_cycle):
    global last_value  # Indicate that you are using the global variable
    percent = int(duty_cycle)
    if percent > 100:
        percent = (percent * 100) // 1023
    if percent < 0:
        percent = 0
    elif percent > 100:
        percent = 100
    previous_value = last_value
    duty_cycle = (percent * 1023) // 100

    if duty_cycle <= 0:
        led_pwm_off()
    else:
        _ensure_led_pwm()
        pwm_led_1.duty(duty_cycle)
        pwm_led_2.duty(duty_cycle)

    if previous_value is None or duty_cycle != previous_value:  # Check if duty_cycle has changed or if it's the first time calling
        print("Changing brightness to {}%".format(percent))
        last_value = duty_cycle  # Update the last_value

    if percent >= FAN_ON_PERCENT:
        fan_pin.on()
    else:
        fan_pin.off()


# ---------- Water level sensor ----------
try:
    water_touch = TouchPad(Pin(WATER_LEVEL_PIN))
except Exception as e:
    water_touch = None
    print("Failed to init water touch sensor:", e)

def read_water_raw(samples=8, delay_ms=10):
    """Return filtered raw touch reading from the water sensor."""
    if water_touch is None:
        return None

    total = 0
    for _ in range(samples):
        total += water_touch.read()
        utime.sleep_ms(delay_ms)
    return total / samples


def read_water_liters(samples=8, delay_ms=10):
    """Return calibrated liquid volume directly from the touch sensor."""
    avg = read_water_raw(samples, delay_ms)
    if avg is None:
        return None
    return raw_to_liters(avg)
