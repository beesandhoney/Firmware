import utime
import time
import machine
import gc
from machine import Pin
import status_led
from captive_portal import CaptivePortal

gpio = None
ntptime = None
asyncio = None
LightIntensities = None
AmbientLightSettings = None
ECSettings = None
load_all_settings_from_file = None
update_ambient_status = None

DEBUG = False
_debug_last_ms = {}


def debug(msg):
    if DEBUG:
        print("MAIN:", msg)


def debug_every(key, msg, interval_ms=5000):
    if not DEBUG:
        return
    now = utime.ticks_ms()
    last = _debug_last_ms.get(key)
    if last is None or utime.ticks_diff(now, last) >= interval_ms:
        _debug_last_ms[key] = now
        print("MAIN:", msg)


debug("main.py import started")

# State Definitions
COSYMODE = 1
TIMECONTROL = 2
SETTINGS = 3

debug("creating CaptivePortal")
portal = CaptivePortal()
debug("CaptivePortal created")

PUMP_ON = True
PUMP_OFF = False
EC_MEASURE_INTERVAL_MS = 5000  # REQ-EC-001: fixed 5s interval
EC_MEASURE_WINDOW_MS = 1000
EC_SETTLE_MS = 200
WATER_MEASURE_INTERVAL_MS = 1000
EC_ALARM_PRINT_INTERVAL_MS = 60000

in_settings_mode = False  # This is the global flag
current_water_volume = None  # Cached calibrated volume for UI / alerts
SETTINGS_AP = None  # Keep a reference so GC doesn't drop the AP object

runtime_initialized = False
pump_pin = None
pump_state = PUMP_OFF  # Keep track of the pump's state
sensor_lock = None  # Created after Wi-Fi startup.
ec_adc = None
ec_active = False  # track EC measurement window to inhibit water depth reads
current_ec_value = None  # Cached EC value for alarms and RGB status indication
current_base_brightness = 0  # latest requested brightness before ALS overrides
ambient_controller = None
_last_ec_alarm_state = None
_last_ec_alarm_print_ms = 0

SETTINGS_FLAG_FILE = "settings_mode.flag"
WIFI_PORTAL_FLAG_FILE = "wifi_portal_mode.flag"
WIFI_RESET_FLAG_FILE = "wifi_reset_mode.flag"
BOOT_HOLD_PIN = 0
MODE_BUTTON_PIN = 26
ON_OFF_DIM_BUTTON_PIN = 17
BOOT_GRACE_MS = 4000
BOOT_POLL_MS = 50
NTP_HOSTS = ("pool.ntp.org", "time.google.com", "time.cloudflare.com")
NTP_RETRY_INTERVAL_S = 30
ENABLE_STA_HTTP_SERVER = False
ALS_STATUS_THRESHOLD = 1500
MODE_DEBOUNCE_MS = 50
DEVICE_PORTAL_PRESS_MS = 2000
COSY_LIGHT_LEVEL = 20

long_press_detector = None
on_off_dim_detector = None


class StartupModeButton:
    def __init__(self, pin_no):
        self.pin = Pin(pin_no, Pin.IN, Pin.PULL_UP)
        self.last_level = self.pin.value()
        self.last_change_ms = utime.ticks_ms()
        self.press_start_ms = None
        self.consumed = False

    def update(self):
        now = utime.ticks_ms()
        level = self.pin.value()
        if level != self.last_level:
            self.last_level = level
            self.last_change_ms = now
            if level == 0:
                self.press_start_ms = now
                self.consumed = False
            else:
                self._handle_release(now)
            return

        if utime.ticks_diff(now, self.last_change_ms) < MODE_DEBOUNCE_MS:
            return
        if level != 0 or self.press_start_ms is None or self.consumed:
            return

        held_ms = utime.ticks_diff(now, self.press_start_ms)
        if held_ms >= DEVICE_PORTAL_PRESS_MS:
            self.consumed = True
            enter_device_portal_callback()

    def _handle_release(self, now):
        if self.press_start_ms is None:
            return
        held_ms = utime.ticks_diff(now, self.press_start_ms)
        self.press_start_ms = None
        if self.consumed:
            return
        if held_ms >= DEVICE_PORTAL_PRESS_MS:
            enter_device_portal_callback()
        else:
            toggle_control_mode_callback()


startup_mode_button = StartupModeButton(MODE_BUTTON_PIN)


class AmbientLightController:
    """Ambient light sampling + LED override with hysteresis and rate limiting."""

    def __init__(self):
        debug("creating AmbientLightController")
        self.adc = gpio.setup_ambient_adc()
        self.window = []
        self.filtered = None
        self.mode = "UNKNOWN"
        self.sensor_fault = False
        self.invalid_count = 0
        self.last_sample_ms = utime.ticks_ms()
        self.last_apply_ms = 0
        self.last_output = None
        debug("AmbientLightController ready")

    def _sample(self):
        raw = gpio.read_ambient_raw(self.adc)
        if raw is None:
            self.invalid_count += 1
            return None

        if raw <= 0 or raw >= 4095:
            self.invalid_count += 1
        else:
            self.invalid_count = 0

        self.window.append(raw)
        max_len = max(1, AmbientLightSettings.settings.get("als_filter_window", 5))
        if len(self.window) > max_len:
            self.window = self.window[-max_len:]

        self.filtered = sum(self.window) / len(self.window)
        return raw

    def _update_mode(self):
        settings = AmbientLightSettings.settings
        t_on = settings["als_threshold_on"]
        t_dim = settings["als_threshold_dim"]

        if self.sensor_fault:
            self.mode = "DIMMED (fault)" if settings.get("als_fault_action") == "dim" else "NORMAL (fault)"
            return

        if self.filtered is None:
            return

        if self.filtered >= t_dim:
            self.mode = "OFF" if settings.get("als_control_mode", "DIM") == "OFF" else "DIMMED"
        elif self.filtered <= t_on:
            self.mode = "NORMAL"
        # else: keep current mode for hysteresis window

    def _apply_brightness(self, target, now_ms, ambient_change=False):
        ramp = AmbientLightSettings.settings.get("als_ramp_rate_ms", 200) if ambient_change else 0
        if target is None:
            return self.last_output

        if self.last_output is None or target != self.last_output:
            if ramp and utime.ticks_diff(now_ms, self.last_apply_ms) < ramp:
                return self.last_output
            gpio.brightnessControl(target)
            self.last_output = target
            self.last_apply_ms = now_ms
        return self.last_output

    def tick(self, base_brightness):
        settings = AmbientLightSettings.settings
        now = utime.ticks_ms()

        if not settings.get("als_enabled", True):
            self.mode = "DISABLED"
            update_ambient_status(None, None, self.mode, False)
            return self._apply_brightness(base_brightness, now)

        if utime.ticks_diff(now, self.last_sample_ms) >= settings["als_sample_interval_ms"]:
            self.last_sample_ms = now
            raw = self._sample()
            self.sensor_fault = self.invalid_count >= 3
            self._update_mode()
            update_ambient_status(raw, self.filtered, self.mode, self.sensor_fault)

        target = base_brightness
        ambient_change = False
        if self.sensor_fault:
            if settings.get("als_fault_action") == "dim":
                target = settings.get("als_dim_level", 0)
                ambient_change = True
        else:
            if self.mode == "DIMMED" or self.mode == "DIMMED (fault)":
                target = settings.get("als_dim_level", base_brightness)
                ambient_change = True
            elif self.mode == "OFF":
                target = 0
                ambient_change = True

        return self._apply_brightness(target, now, ambient_change)


def current_hour():
    """Return the current hour using RTC (0-23)."""
    return utime.localtime()[3]

def start_settings_ap():
    import network
    import utime
    essid = b"GrowSettings"
    channel = 1  # force a safe channel so laptops (esp. US region) see the SSID

    # Bring up a very simple open AP, retrying in case the Wi-Fi driver is
    # still in a bad state right after boot (same 0x0101 we saw elsewhere).
    for attempt in range(1, 4):
        try:
            print("Settings AP: attempt", attempt)
            reset_wifi_state()

            sta = network.WLAN(network.STA_IF)
            ap = network.WLAN(network.AP_IF)

            try:
                sta.active(False)
            except Exception:
                pass

            ap.active(False)
            utime.sleep_ms(100)
            ap.active(True)
            print("Settings AP: ap.active()->", ap.active())

            # Wait until the AP reports active to avoid config() failures
            for _ in range(10):
                if ap.active():
                    break
                utime.sleep_ms(100)

            if not ap.active():
                raise Exception("AP failed to become active")

            ap.config(essid=essid, authmode=network.AUTH_OPEN, channel=channel)
            ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))

            # Let the AP actually start beaconing before continuing
            utime.sleep_ms(300)

            print("Settings AP configured:")
            print("  ESSID:", ap.config("essid"))
            print("  IFCONFIG:", ap.ifconfig())
            return ap
        except Exception as e:
            print("GrowSettings AP start failed (attempt {}/3): {}".format(attempt, e))
            utime.sleep_ms(300)

    print("ERROR: Could not start GrowSettings AP after retries")
    return None


def reset_wifi_state():
    import network, utime

    for iface in (network.STA_IF, network.AP_IF):
        try:
            wlan = network.WLAN(iface)
            wlan.active(False)
        except Exception:
            pass

    utime.sleep_ms(300)



def settings_mode_requested():
    try:
        with open(SETTINGS_FLAG_FILE, "r") as f:
            val = f.read().strip()
        debug("{}={}".format(SETTINGS_FLAG_FILE, val))
        return val == "1"
    except OSError:
        debug("{} not present".format(SETTINGS_FLAG_FILE))
        return False

def clear_settings_flag():
    import os
    try:
        os.remove(SETTINGS_FLAG_FILE)
    except OSError:
        pass


def mode_flag_requested(flag_file):
    try:
        with open(flag_file, "r") as f:
            val = f.read().strip()
        debug("{}={}".format(flag_file, val))
        return val == "1"
    except OSError:
        debug("{} not present".format(flag_file))
        return False


def set_mode_flag(flag_file):
    try:
        with open(flag_file, "w") as f:
            f.write("1")
        return True
    except Exception as e:
        print("Failed to set mode flag {}: {}".format(flag_file, e))
        return False


def clear_mode_flag(flag_file):
    import os
    try:
        os.remove(flag_file)
        debug("cleared {}".format(flag_file))
    except OSError:
        debug("{} already clear".format(flag_file))
        pass


def load_settings_from_file(filename='settings.json'):
    try:
        debug("loading settings from {}".format(filename))
        load_all_settings_from_file(filename)
        debug("settings loaded; light={}, ambient={}, ec={}".format(
            LightIntensities.settings,
            AmbientLightSettings.settings,
            ECSettings.settings,
        ))
        return LightIntensities.settings
    except Exception as e:
        print("Failed to load settings:", e)
        # Return default settings or handle the error appropriately
        return LightIntensities.settings


def wait_for_boot_hold(grace_ms=BOOT_GRACE_MS, pin_no=BOOT_HOLD_PIN):
    """
    Provide a short boot grace period where holding GPIO0 pauses startup.
    Release the button to continue normal boot.
    """
    btn = machine.Pin(pin_no, machine.Pin.IN, machine.Pin.PULL_UP)
    print("Boot grace active for {} ms on GPIO{}".format(grace_ms, pin_no))
    print("Hold BOOT button to pause startup")

    deadline = utime.ticks_add(utime.ticks_ms(), grace_ms)
    while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
        if not btn.value():
            print("Boot paused on GPIO{} (release to continue)".format(pin_no))
            while not btn.value():
                time.sleep(0.1)
            print("Boot pause released, resuming startup")
            return True
        time.sleep_ms(BOOT_POLL_MS)
    return False


def service_startup_controls():
    """
    Keep physical controls responsive while Wi-Fi startup is running before
    the asyncio main loop exists.
    """
    startup_mode_button.update()
    if long_press_detector:
        long_press_detector.update()
    if on_off_dim_detector:
        on_off_dim_detector.update()


def sync_time_once(attempt_index):
    """Single NTP attempt to avoid blocking the main loop."""
    host = NTP_HOSTS[attempt_index % len(NTP_HOSTS)]
    try:
        gc.collect()
        ntptime.host = host
        print("NTP sync attempt {} via {}".format(attempt_index + 1, host))
        ntptime.settime()
        print("NTP sync OK")
        return True
    except OSError as e:
        err = e.args[0] if e.args else e
        print("NTP sync failed via {} (OSError: {})".format(host, err))
        return False


async def pump_control():
    global pump_state
    debug("pump_control task started")
    while True:
        pump_pin.value(PUMP_ON)
        pump_state = PUMP_ON  # Update the pump's state
        print("pump is on")
        await asyncio.sleep(120)  # Keep pump ON for 2 minutes
        
        pump_pin.value(PUMP_OFF)
        pump_state = PUMP_OFF  # Update the pump's state
        print("pump is off")
        await asyncio.sleep(1200)  # Keep pump OFF for 20 minutes

        
async def measure_ec():
    global ec_active, current_ec_value
    debug("measure_ec task started; interval={}ms, window={}ms".format(EC_MEASURE_INTERVAL_MS, EC_MEASURE_WINDOW_MS))
    last_measure_ms = utime.ticks_ms() - EC_MEASURE_INTERVAL_MS
    while True:
        now = utime.ticks_ms()
        elapsed = utime.ticks_diff(now, last_measure_ms)

        if elapsed >= EC_MEASURE_INTERVAL_MS:
            debug("starting EC measurement window")
            last_measure_ms = now
            ec_active = True
            window_start = utime.ticks_ms()
            ec_value = None
            try:
                async with sensor_lock:
                    gpio.set_ec_power(True)
                    await asyncio.sleep_ms(EC_SETTLE_MS)
                    ec_value = gpio.read_ec_value(ec_adc)
                    remaining_ms = EC_MEASURE_WINDOW_MS - utime.ticks_diff(utime.ticks_ms(), window_start)
                    if remaining_ms > 0:
                        await asyncio.sleep_ms(remaining_ms)
            finally:
                gpio.set_ec_power(False)
                ec_active = False
                debug("EC measurement window complete")

            current_ec_value = ec_value

            low_lim = ECSettings.settings.get("ec_low_alarm_us", 0)
            high_lim = ECSettings.settings.get("ec_high_alarm_us", 999999)
            if ec_value is not None:
                status_led.set_ec_value(ec_value, low_lim, high_lim)
                report_ec_alarm(ec_value, low_lim, high_lim)

        await asyncio.sleep(0.1)  # Keep loop responsive while rate-limiting EC reads


def report_ec_alarm(ec_value, low_lim, high_lim):
    global _last_ec_alarm_state, _last_ec_alarm_print_ms

    if ec_value < low_lim:
        alarm_state = "low"
        message = "EC alarm: below low limit {} uS/cm".format(low_lim)
    elif ec_value > high_lim:
        alarm_state = "high"
        message = "EC alarm: above high limit {} uS/cm".format(high_lim)
    else:
        alarm_state = None
        message = None

    now = utime.ticks_ms()
    if alarm_state is None:
        if _last_ec_alarm_state is not None:
            print("EC alarm cleared")
        _last_ec_alarm_state = None
        _last_ec_alarm_print_ms = now
        return

    due = utime.ticks_diff(now, _last_ec_alarm_print_ms) >= EC_ALARM_PRINT_INTERVAL_MS
    if alarm_state != _last_ec_alarm_state or due:
        print(message)
        _last_ec_alarm_state = alarm_state
        _last_ec_alarm_print_ms = now



async def monitor_water_level():
    global current_water_volume
    debug("monitor_water_level task started")
    while True:
        if sensor_lock.locked() or ec_active:
            await asyncio.sleep_ms(200)
            continue

        async with sensor_lock:
            volume_l = gpio.read_water_liters()
        current_water_volume = volume_l
        gpio.update_water_level_indicator(volume_l)
        await asyncio.sleep_ms(WATER_MEASURE_INTERVAL_MS)


async def supervise_task(name, coroutine):
    """Convert an unexpected runtime task failure into a system error state."""
    try:
        await coroutine
    except Exception as e:
        print("SYSTEM ERROR in {}: {}".format(name, e))
        status_led.set_system_error()
        raise


hour = 0
oldHour = -1
time_synced = False
last_in_settings_mode = False


def cosyLightControl():
    return COSY_LIGHT_LEVEL


def load_runtime_modules():
    global gpio, ntptime, asyncio
    global LightIntensities, AmbientLightSettings, ECSettings
    global load_all_settings_from_file, update_ambient_status

    if gpio is not None:
        return

    debug("loading runtime modules")
    gc.collect()

    import gpio as gpio_mod
    import ntptime as ntptime_mod
    import uasyncio as asyncio_mod
    from shared_settings import (
        LightIntensities as LightIntensities_mod,
        AmbientLightSettings as AmbientLightSettings_mod,
        ECSettings as ECSettings_mod,
        load_all_settings_from_file as load_all_settings_from_file_mod,
        update_ambient_status as update_ambient_status_mod,
    )

    gpio = gpio_mod
    ntptime = ntptime_mod
    asyncio = asyncio_mod
    LightIntensities = LightIntensities_mod
    AmbientLightSettings = AmbientLightSettings_mod
    ECSettings = ECSettings_mod
    load_all_settings_from_file = load_all_settings_from_file_mod
    update_ambient_status = update_ambient_status_mod


def init_runtime_controls():
    global long_press_detector, on_off_dim_detector

    if long_press_detector is not None and on_off_dim_detector is not None:
        return

    long_press_detector = gpio.LongPressDetector(
        gpio.mode_button_pin,
        short_press_callback=toggle_control_mode_callback,
        long_press_callback=enter_device_portal_callback,
        debounce_ms=50,
        settings_press_min=DEVICE_PORTAL_PRESS_MS,
    )
    debug("mode button detector ready on GPIO{}".format(gpio.MODE_BUTTON_PIN))

    on_off_dim_detector = gpio.LongPressDetector(
        gpio.on_off_dim_button_pin,
        short_press_callback=toggle_control_mode_callback,
        debounce_ms=50,
    )
    debug("on/off/dim button detector ready on GPIO{}".format(gpio.ON_OFF_DIM_BUTTON_PIN))


def init_runtime_hardware():
    global runtime_initialized, pump_pin, sensor_lock, ec_adc
    global ambient_controller, current_base_brightness, hour

    if runtime_initialized:
        return

    debug("initializing runtime hardware")
    load_runtime_modules()
    gc.collect()

    pump_pin = Pin(13, Pin.OUT)
    debug("pump pin initialized on GPIO13")

    sensor_lock = asyncio.Lock()
    debug("creating EC ADC")
    ec_adc = gpio.setup_adc()
    debug("EC ADC ready")

    LightIntensities.settings = load_settings_from_file()
    ambient_controller = AmbientLightController()
    current_base_brightness = LightIntensities.settings.get("off", 0)
    hour = current_hour()
    init_runtime_controls()
    runtime_initialized = True
    debug("runtime hardware initialized; base brightness={}".format(current_base_brightness))

def timeControl(hour, oldHour):
    update = False
    brightness = LightIntensities.settings['off']
    if hour == 5:
        brightness = LightIntensities.settings['morning']
        message = "morning"
    elif 5 < hour < 17:
        brightness = LightIntensities.settings['daylight']
        message = "daylight"
    elif 17 <= hour < 19:
        brightness = LightIntensities.settings['evening']
        message = "evening"
    else:
        brightness = LightIntensities.settings['off']
        message = "off"

    if oldHour != hour:
        print(message)
        update = True
    return brightness, update

state = TIMECONTROL


def toggle_control_mode_callback():
    global state, current_base_brightness
    if state == COSYMODE:
        state = TIMECONTROL
        print("Went to time control")
    elif state == TIMECONTROL:
        state = COSYMODE
        current_base_brightness = COSY_LIGHT_LEVEL
        print("Went to cosy mode")


def prepare_for_mode_reset(reason):
    print("Preparing for reset:", reason)
    try:
        if gpio is not None:
            gpio.led_pwm_off()
    except Exception:
        pass
    try:
        portal.cleanup(keep_sta=False)
    except Exception as e:
        print("WiFi cleanup before reset failed:", e)
    try:
        gc.collect()
    except Exception:
        pass
    utime.sleep_ms(1200)


def reboot_clean(reason="mode change"):
    print("Rebooting cleanly:", reason)
    try:
        gc.collect()
    except Exception:
        pass
    try:
        machine.deepsleep(250)
    except Exception as e:
        print("Clean reboot fallback:", e)
        machine.reset()


# This callback is used by the lightweight captive_http settings server.
def exit_settings_callback():
    print("Settings saved, rebooting to normal mode...")
    prepare_for_mode_reset("exit settings")
    reboot_clean("normal mode")

    
def enter_device_portal_callback():
    print("Button >=2s: request Device Portal")
    if not set_mode_flag(WIFI_PORTAL_FLAG_FILE):
        return
    prepare_for_mode_reset("Device Portal mode")
    reboot_clean("Device Portal mode")
    
def update_runtime_status_led():
    connected = bool(portal.sta_if and portal.sta_if.isconnected())
    status_led.set_wifi_connected(connected)
    status_led.tick()


async def main_logic():
    global oldHour, state, in_settings_mode, time_synced, last_in_settings_mode, current_base_brightness
    debug("main_logic task started")
    ntp_attempt_index = 0
    next_ntp_retry_ms = 0

    while True:
        hour = current_hour()

        # Always let the long-press logic run
        long_press_detector.update()
        on_off_dim_detector.update()
        if ENABLE_STA_HTTP_SERVER:
            portal.poll_http(0)

        base_brightness = current_base_brightness

        if last_in_settings_mode and not in_settings_mode:
            print("Exited settings mode; resuming main loop")
        last_in_settings_mode = in_settings_mode

        # Sync time once Wi-Fi is actually up
        if not time_synced:
            try:
                sta = portal.sta_if
                now_ms = utime.ticks_ms()
                if sta and sta.isconnected() and utime.ticks_diff(now_ms, next_ntp_retry_ms) >= 0:
                    if sync_time_once(ntp_attempt_index):
                        time_synced = True
                    else:
                        ntp_attempt_index += 1
                        next_ntp_retry_ms = utime.ticks_add(now_ms, NTP_RETRY_INTERVAL_S * 1000)
            except Exception as e:
                print("Time sync check failed:", e)

        if not in_settings_mode:
            if state == SETTINGS:
                # settings handled by web server
                pass
            elif state == COSYMODE:
                base_brightness = cosyLightControl()
            elif state == TIMECONTROL:
                brightness, updated = timeControl(hour, oldHour)
                base_brightness = brightness
                if updated:
                    oldHour = hour

        current_base_brightness = base_brightness
        if current_ec_value is not None:
            status_led.set_ec_value(
                current_ec_value,
                ECSettings.settings.get("ec_low_alarm_us", 500),
                ECSettings.settings.get("ec_high_alarm_us", 2000),
            )
        ambient_controller.tick(base_brightness)
        update_runtime_status_led()

        await asyncio.sleep(0.05)


def run():
    import time
    debug("run() entered")
    status_led.initialize()
    time.sleep_ms(500)
    debug("boot grace starting")
    wait_for_boot_hold()
    debug("boot grace complete")

    if settings_mode_requested():
        print("Booting in SETTINGS MODE")
        clear_settings_flag()

        # Run settings via the same lightweight captive portal server
        print("Settings mode: launching captive portal server for settings UI")
        # allow HTTP handler to reset back to normal mode
        portal.exit_callback = exit_settings_callback
        portal.service_callback = service_startup_controls
        if not portal.captive_portal(mode="settings"):
            print("Settings portal failed to start")
            reboot_clean("settings portal failed")
    else:
        print("Booting in NORMAL MODE")
        force_ap = False
        if mode_flag_requested(WIFI_RESET_FLAG_FILE):
            print("Boot flag: WIFI RESET MODE")
            clear_mode_flag(WIFI_RESET_FLAG_FILE)
            try:
                portal.creds.remove()
            except Exception as e:
                print("Failed to remove WiFi creds during reset boot:", e)
            force_ap = True
        elif mode_flag_requested(WIFI_PORTAL_FLAG_FILE):
            print("Boot flag: WIFI PORTAL MODE")
            clear_mode_flag(WIFI_PORTAL_FLAG_FILE)
            force_ap = True

        debug("starting portal; force_ap={}".format(force_ap))
        portal.exit_callback = exit_settings_callback
        portal.service_callback = service_startup_controls
        wifi_ready = portal.start(force_ap=force_ap)
        debug("portal.start returned")

        if force_ap and not wifi_ready:
            print("Forced Device Portal failed; rebooting instead of starting runtime")
            reboot_clean("Device Portal startup failed")

        if wifi_ready and ENABLE_STA_HTTP_SERVER:
            portal.start_sta_server()
        elif wifi_ready:
            print("STA HTTP server disabled; use mode button for settings portal")
        else:
            print("WiFi startup did not complete; continuing offline without HTTP server")

        init_runtime_hardware()
        status_led.set_operation_started()
        status_led.set_wifi_connected(wifi_ready)

        loop = asyncio.get_event_loop()
        debug("scheduling tasks")
        loop.create_task(supervise_task("main_logic", main_logic()))
        loop.create_task(supervise_task("pump_control", pump_control()))
        loop.create_task(supervise_task("measure_ec", measure_ec()))
        loop.create_task(supervise_task("monitor_water_level", monitor_water_level()))
        debug("starting event loop")
        loop.run_forever()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print("SYSTEM ERROR:", e)
        status_led.set_system_error()
        raise
else:
    debug("main.py import complete; app not started. Run with: import main; main.run()")


    
    
    
