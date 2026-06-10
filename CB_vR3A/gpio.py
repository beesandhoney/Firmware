from machine import Pin, PWM, Timer, ADC, TouchPad
import utime
from time import sleep_ms
import uasyncio as asyncio
from shared_settings import WaterCalibration
from level_indicator import LevelIndicator


EC_LED_PIN = 15
EC_ADC_PIN = 35
EC_POWER_PIN = 5
TEMP_ADC_PIN = 32
MODE_BUTTON_PIN = 26
PHOTO_ADC_PIN = 34
FAN_PIN = 25
ON_OFF_DIM_BUTTON_PIN = 17
PUMP1_PIN = 13
WATER_LEVEL_PIN = 27
LEVEL_DATA_PIN = 4
LEVEL_CLOCK_PIN = 14
LEVEL_LATCH_PIN = 22

BUZZER_PIN = None
buzzer = None
buzzer_mode = None  # None / "ec" / "water"
buzzer_task_running = False
# ESP32 timers are limited to IDs 0-3; use a dedicated slot for the failsafe.
buzzer_off_timer = Timer(3)  # Failsafe timer to turn buzzer off if loop stalls
BUZZER_FAILSAFE_MS = 1500
EC_LOW_THRESHOLD = 300
EC_HIGH_THRESHOLD = 2000
WATER_LOW_THRESHOLD_MM = 50


def _set_buzzer_output(duty, freq=None):
    """Set buzzer output with a hardware failsafe to avoid stuck beeps."""
    if buzzer is None:
        return
    if freq is not None:
        buzzer.freq(freq)
    buzzer.duty(duty)
    if duty > 0:
        # Arm a one-shot timer that will shut the buzzer off if the loop stalls.
        buzzer_off_timer.init(
            period=BUZZER_FAILSAFE_MS,
            mode=Timer.ONE_SHOT,
            callback=lambda t: buzzer.duty(0)
        )
    else:
        buzzer_off_timer.deinit()


async def _buzzer_worker():
    """Run a pattern based on the current buzzer_mode."""
    global buzzer_task_running
    buzzer_task_running = True
    while buzzer_mode:
        if buzzer_mode == "water":
            # Short beeps for low water level
            _set_buzzer_output(duty=512, freq=600)
            await asyncio.sleep(0.2)
            _set_buzzer_output(duty=0)
            await asyncio.sleep(0.2)
        elif buzzer_mode == "ec":
            # Alternating tones for EC warning
            _set_buzzer_output(duty=512, freq=1000)
            await asyncio.sleep(0.5)
            _set_buzzer_output(duty=512, freq=2000)
            await asyncio.sleep(0.5)
    _set_buzzer_output(duty=0)
    buzzer_task_running = False


def _set_buzzer_mode(mode):
    """Start or stop the buzzer worker with simple priority (water > ec)."""
    global buzzer_mode

    # Water warning always wins except when we explicitly clear it (mode None)
    if buzzer_mode == "water" and mode not in (None, "water"):
        return

    if buzzer_mode == mode:
        return

    buzzer_mode = mode
    if mode and not buzzer_task_running:
        asyncio.create_task(_buzzer_worker())


def control_buzzer(ec_value, low_threshold=EC_LOW_THRESHOLD, high_threshold=EC_HIGH_THRESHOLD):
    """Control the buzzer based on EC value (ignored if water alarm is active)."""
    if buzzer is None:
        return
    if buzzer_mode == "water":
        return

    if ec_value < low_threshold or ec_value > high_threshold:
        _set_buzzer_mode("ec")
    else:
        if buzzer_mode == "ec":
            _set_buzzer_mode(None)


def control_water_buzzer(depth_mm, low_threshold=WATER_LOW_THRESHOLD_MM):
    """Trigger a distinct buzzer pattern when water depth is too low."""
    if buzzer is None:
        return
    if depth_mm is None:
        return
    if depth_mm < low_threshold:
        _set_buzzer_mode("water")
    else:
        if buzzer_mode == "water":
            _set_buzzer_mode(None)

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

def read_ec_value(adc):
    voltage = adc.read() * (3.3 / 4095)  # Convert ADC reading to voltage
    # Convert voltage to EC value
    # This is a placeholder for the actual conversion formula you have.
    # You will need to replace the conversion_factor and offset with actual values provided by your sensor's datasheet.
    conversion_factor = 1013  # Example conversion factor
    offset = 0.0  # Example offset
    ec_value = voltage * conversion_factor + offset
    return ec_value

ec_power_pin = Pin(EC_POWER_PIN, Pin.OUT)
ec_led_pin = Pin(EC_LED_PIN, Pin.OUT)
fan_pin = Pin(FAN_PIN, Pin.OUT)
mode_button_pin = Pin(MODE_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
on_off_dim_button_pin = Pin(ON_OFF_DIM_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
button_pin = mode_button_pin
_last_button_level = 1
_last_button_change_ms = 0
_button_block_until = 0

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
    value = 1 if enabled else 0
    ec_power_pin.value(value)
    ec_led_pin.value(value)


def update_ec_indicator(ec_value, low_threshold=EC_LOW_THRESHOLD, high_threshold=EC_HIGH_THRESHOLD):
    if ec_value is None:
        ec_led_pin.off()
    elif ec_value < low_threshold or ec_value > high_threshold:
        ec_led_pin.value(utime.ticks_ms() // 250 % 2)
    else:
        ec_led_pin.on()


def update_water_level_indicator(depth_mm):
    if level_indicator is None:
        return
    if depth_mm is None:
        level_indicator.clear()
        return
    try:
        count = int((float(depth_mm) * 8) / WaterCalibration.DEPTH_POINTS[-1])
        if depth_mm > 0 and count < 1:
            count = 1
        level_indicator.bar(count)
    except Exception as e:
        print("Failed to update water level indicator:", e)


def encoderIsPushed():
    """Return True once per press without blocking the event loop."""
    global _last_button_level, _last_button_change_ms, _button_block_until

    DEBOUNCE_DELAY = 20  # ms
    BLOCK_TIME = 300     # ms

    now = utime.ticks_ms()

    # Ignore during block window after a detected press
    if utime.ticks_diff(now, _button_block_until) < 0:
        return False

    level = button_pin.value()
    if level != _last_button_level:
        _last_button_level = level
        _last_button_change_ms = now

    # Active-low press after debounce window
    if level == 0 and utime.ticks_diff(now, _last_button_change_ms) >= DEBOUNCE_DELAY:
        _button_block_until = utime.ticks_add(now, BLOCK_TIME)
        return True

    return False

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
    
def encoderRotarySetup():
    return None

def encoderRotaryVal(r):
    return 0
   



last_value = None  # Initialize the last_value outside the function

pwm2 = PWM(Pin(2), freq=5000)
pwm18 = PWM(Pin(18), freq=5000)

def brightnessControl(duty_cycle):
    global last_value  # Indicate that you are using the global variable

    
    pwm2.duty(duty_cycle)

    
    pwm18.duty(duty_cycle)

    if last_value is None or duty_cycle != last_value:  # Check if duty_cycle has changed or if it's the first time calling
        print("Changing brightness to {}".format(duty_cycle))
        last_value = duty_cycle  # Update the last_value

    if duty_cycle > 350:
        fan_pin.on()
    else:
        fan_pin.off()


# ---------- Water level sensor ----------
try:
    water_touch = TouchPad(Pin(WATER_LEVEL_PIN))
except Exception as e:
    water_touch = None
    print("Failed to init water touch sensor:", e)

# (raw_reading, depth_mm)
water_lookup_table = [
    (100, 0),
    (80, 25),
    (59, 50),
    (55, 100),
    (42, 250)
]


def _interpolate_calibrated_water_depth(reading):
    points = []
    for point in WaterCalibration.points:
        try:
            raw = point.get("raw_depth_mm", None)
            if raw is None:
                continue
            points.append((float(raw), float(point["depth_mm"])))
        except Exception:
            pass

    if len(points) < 2:
        return None

    points.sort(key=lambda p: p[1])
    for i in range(len(points) - 1):
        if points[i + 1][0] >= points[i][0]:
            return None

    points.sort(key=lambda p: p[0], reverse=True)
    if reading >= points[0][0]:
        return points[0][1]
    if reading <= points[-1][0]:
        return points[-1][1]

    for i in range(len(points) - 1):
        r0, d0 = points[i]
        r1, d1 = points[i + 1]
        if r1 <= reading <= r0:
            if r1 == r0:
                return d0
            return d0 + (reading - r0) * (d1 - d0) / (r1 - r0)

    return None


def water_raw_to_depth_mm(reading):
    calibrated = _interpolate_calibrated_water_depth(reading)
    if calibrated is not None:
        return calibrated

    for i in range(len(water_lookup_table) - 1):
        r0, d0 = water_lookup_table[i]
        r1, d1 = water_lookup_table[i + 1]
        if r1 <= reading <= r0:
            # Linear interpolation
            return d0 + (reading - r0) * (d1 - d0) / (r1 - r0)

    # Clamp outside table
    if reading > water_lookup_table[0][0]:
        return water_lookup_table[0][1]
    elif reading < water_lookup_table[-1][0]:
        return water_lookup_table[-1][1]


def read_water_raw(samples=8, delay_ms=10):
    """Return filtered raw touch reading from the water sensor."""
    if water_touch is None:
        return None

    total = 0
    for _ in range(samples):
        total += water_touch.read()
        utime.sleep_ms(delay_ms)
    return total / samples


def read_water_depth_mm(samples=8, delay_ms=10):
    """Return filtered water depth in mm from the touch sensor."""
    avg = read_water_raw(samples, delay_ms)
    if avg is None:
        return None
    return water_raw_to_depth_mm(avg)
