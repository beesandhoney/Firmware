"""Central controller for the operation LED and NeoPixel status LED."""

import utime

Pin = None
neopixel = None

OPERATION_LED_PIN = 15
STATUS_RGB_LED_PIN = 21
STATUS_RGB_LED_COUNT = 1

STATUS_NORMAL = "normal"
STATUS_WIFI_CONNECTING = "wifi_connecting"
STATUS_WIFI_CONNECTION_FAILED = "wifi_connection_failed"
STATUS_CONFIG_PORTAL = "config_portal"
STATUS_EC_LOW = "ec_low"
STATUS_EC_HIGH = "ec_high"
STATUS_SYSTEM_ERROR = "system_error"
EVENT_SETTINGS_SAVED = "settings_saved"

COLOR_OFF = (0, 0, 0)
COLOR_BLUE = (0, 0, 40)
COLOR_GREEN = (0, 40, 0)
COLOR_YELLOW = (40, 30, 0)
COLOR_RED = (40, 0, 0)

_PERSISTENT_STATES = (
    STATUS_SYSTEM_ERROR,
    STATUS_CONFIG_PORTAL,
    STATUS_EC_HIGH,
    STATUS_EC_LOW,
    STATUS_WIFI_CONNECTION_FAILED,
    STATUS_WIFI_CONNECTING,
)

_active = {}
_status_started_ms = {}
_operation_pin = None
STATUS_RGB_LED = None
_operation_disabled = False
_rgb_disabled = False
_operation_started = False
_wifi_connected = False
_operation_phase_started_ms = 0
_event = None
_event_started_ms = 0
_last_operation_value = None
_last_color = None
_transport_configured = False


def _load_pin():
    global Pin
    if Pin is not None:
        return True
    try:
        from machine import Pin as pin_mod
        Pin = pin_mod
        return True
    except Exception as e:
        print("Status LED GPIO unavailable:", e)
        return False


def _load_neopixel():
    global neopixel
    if neopixel is not None:
        return True
    try:
        import neopixel as neopixel_mod
        neopixel = neopixel_mod
        return True
    except Exception as e:
        print("NeoPixel driver unavailable:", e)
        return False


def _configure_neopixel_transport():
    """Avoid per-write ESP-IDF RMT allocations for the single status pixel."""
    global _transport_configured
    if _transport_configured:
        return
    _transport_configured = True
    try:
        from esp32 import RMT
        if hasattr(RMT, "bitstream_rmt"):
            RMT.bitstream_rmt(False)
        elif hasattr(RMT, "bitstream_channel"):
            # MicroPython <= 1.26 uses None to select bit-banging.
            RMT.bitstream_channel(None)
    except Exception as e:
        # Non-ESP32 test environments and older ports can safely use their
        # default NeoPixel transport.
        print("NeoPixel transport configuration skipped:", e)


def _ensure_operation_led():
    global _operation_pin, _operation_disabled
    if _operation_disabled:
        return None
    if _operation_pin is None:
        if not _load_pin():
            _operation_disabled = True
            return None
        try:
            _operation_pin = Pin(OPERATION_LED_PIN, Pin.OUT)
        except Exception as e:
            _operation_disabled = True
            print("Operation LED disabled:", e)
    return _operation_pin


def _ensure_rgb_led():
    global STATUS_RGB_LED, _rgb_disabled
    if _rgb_disabled:
        return None
    if STATUS_RGB_LED is None:
        if not _load_pin() or not _load_neopixel():
            _rgb_disabled = True
            return None
        try:
            _configure_neopixel_transport()
            STATUS_RGB_LED = neopixel.NeoPixel(
                Pin(STATUS_RGB_LED_PIN, Pin.OUT), STATUS_RGB_LED_COUNT
            )
        except Exception as e:
            _rgb_disabled = True
            print("RGB status LED disabled:", e)
    return STATUS_RGB_LED


def _write_operation(value, force=False):
    global _last_operation_value
    value = 1 if value else 0
    if not force and value == _last_operation_value:
        return
    pin = _ensure_operation_led()
    if pin is None:
        return
    try:
        pin.value(value)
        _last_operation_value = value
    except Exception as e:
        print("Operation LED write failed:", e)


def _write_rgb(color, force=False):
    global _last_color, _rgb_disabled
    if not force and color == _last_color:
        return
    pixel = _ensure_rgb_led()
    if pixel is None:
        return
    try:
        pixel[0] = color
        pixel.write()
        _last_color = color
    except Exception as e:
        _rgb_disabled = True
        print("RGB status LED write failed:", e)


def initialize():
    """Put both LEDs in their required startup OFF state."""
    global _operation_started, _wifi_connected, _event
    global _operation_phase_started_ms
    _active.clear()
    _status_started_ms.clear()
    _operation_started = False
    _wifi_connected = False
    _event = None
    _operation_phase_started_ms = utime.ticks_ms()
    _write_operation(0, force=True)
    _write_rgb(COLOR_OFF, force=True)


def set_operation_started(started=True):
    """Enable operation indication after basic initialization completes."""
    global _operation_started, _operation_phase_started_ms
    started = bool(started)
    if started != _operation_started:
        _operation_started = started
        _operation_phase_started_ms = utime.ticks_ms()
    tick()


def set_wifi_connected(connected):
    global _wifi_connected, _operation_phase_started_ms
    connected = bool(connected)
    if connected != _wifi_connected:
        _wifi_connected = connected
        _operation_phase_started_ms = utime.ticks_ms()
    if connected:
        _active.pop(STATUS_WIFI_CONNECTING, None)
        _active.pop(STATUS_WIFI_CONNECTION_FAILED, None)
        _status_started_ms.pop(STATUS_WIFI_CONNECTING, None)
        _status_started_ms.pop(STATUS_WIFI_CONNECTION_FAILED, None)
    tick()


def set_status(state, active=True):
    """Publish or clear a persistent logical state."""
    if state == STATUS_NORMAL:
        if active:
            _active.clear()
            _status_started_ms.clear()
        tick()
        return
    if state not in _PERSISTENT_STATES:
        raise ValueError("Unknown status: {}".format(state))
    if active:
        if state == STATUS_WIFI_CONNECTING:
            _active.pop(STATUS_WIFI_CONNECTION_FAILED, None)
            _status_started_ms.pop(STATUS_WIFI_CONNECTION_FAILED, None)
        elif state == STATUS_WIFI_CONNECTION_FAILED:
            _active.pop(STATUS_WIFI_CONNECTING, None)
            _status_started_ms.pop(STATUS_WIFI_CONNECTING, None)
        elif state == STATUS_EC_LOW:
            _active.pop(STATUS_EC_HIGH, None)
            _status_started_ms.pop(STATUS_EC_HIGH, None)
        elif state == STATUS_EC_HIGH:
            _active.pop(STATUS_EC_LOW, None)
            _status_started_ms.pop(STATUS_EC_LOW, None)
        if not _active.get(state):
            _status_started_ms[state] = utime.ticks_ms()
        _active[state] = True
    else:
        _active.pop(state, None)
        _status_started_ms.pop(state, None)
    tick()


def set_connecting():
    set_wifi_connected(False)
    set_status(STATUS_WIFI_CONNECTING)


def set_connection_failed():
    set_wifi_connected(False)
    set_status(STATUS_WIFI_CONNECTION_FAILED)


def set_connected():
    set_wifi_connected(True)


def set_config_portal(active=True):
    set_status(STATUS_CONFIG_PORTAL, active)


def set_ec_value(value, low_threshold, high_threshold):
    new_state = None
    if value is None:
        pass
    elif value < low_threshold:
        new_state = STATUS_EC_LOW
    elif value > high_threshold:
        new_state = STATUS_EC_HIGH

    for state in (STATUS_EC_LOW, STATUS_EC_HIGH):
        if state != new_state:
            _active.pop(state, None)
            _status_started_ms.pop(state, None)
    if new_state is not None and not _active.get(new_state):
        _active[new_state] = True
        _status_started_ms[new_state] = utime.ticks_ms()
    tick()


def set_system_error(active=True):
    if active:
        set_status(STATUS_SYSTEM_ERROR)
        _write_operation(0)
    else:
        set_status(STATUS_SYSTEM_ERROR, False)


def trigger_event(event):
    global _event, _event_started_ms
    if event != EVENT_SETTINGS_SAVED:
        raise ValueError("Unknown status event: {}".format(event))
    if _active.get(STATUS_SYSTEM_ERROR):
        return False
    _event = event
    _event_started_ms = utime.ticks_ms()
    tick()
    return True


def settings_saved():
    return trigger_event(EVENT_SETTINGS_SAVED)


def off():
    """Return the RGB LED to normal; operation indication is unaffected."""
    set_status(STATUS_NORMAL)


def _persistent_color(now):
    if _active.get(STATUS_SYSTEM_ERROR):
        return COLOR_RED
    if _active.get(STATUS_CONFIG_PORTAL):
        elapsed = utime.ticks_diff(now, _status_started_ms[STATUS_CONFIG_PORTAL])
        return COLOR_BLUE if elapsed % 1000 < 500 else COLOR_GREEN
    if _active.get(STATUS_EC_HIGH):
        elapsed = utime.ticks_diff(now, _status_started_ms[STATUS_EC_HIGH])
        return COLOR_YELLOW if elapsed % 400 < 200 else COLOR_OFF
    if _active.get(STATUS_EC_LOW):
        elapsed = utime.ticks_diff(now, _status_started_ms[STATUS_EC_LOW])
        return COLOR_YELLOW if elapsed % 2000 < 500 else COLOR_OFF
    if _active.get(STATUS_WIFI_CONNECTION_FAILED):
        elapsed = utime.ticks_diff(now, _status_started_ms[STATUS_WIFI_CONNECTION_FAILED])
        return COLOR_BLUE if elapsed % 400 < 200 else COLOR_OFF
    if _active.get(STATUS_WIFI_CONNECTING):
        elapsed = utime.ticks_diff(now, _status_started_ms[STATUS_WIFI_CONNECTING])
        return COLOR_BLUE if elapsed % 1000 < 500 else COLOR_OFF
    return COLOR_OFF


def _event_color(now):
    global _event
    if _event != EVENT_SETTINGS_SAVED:
        return None
    elapsed = utime.ticks_diff(now, _event_started_ms)
    if elapsed < 0 or elapsed >= 900:
        _event = None
        return None
    return COLOR_GREEN if (elapsed // 150) % 2 == 0 else COLOR_OFF


def tick(force=False):
    """Advance both LED patterns; call at least every 50-100 ms."""
    now = utime.ticks_ms()
    if not _operation_started or _active.get(STATUS_SYSTEM_ERROR):
        operation_value = 0
    elif _wifi_connected:
        operation_value = 1
    else:
        elapsed = utime.ticks_diff(now, _operation_phase_started_ms)
        operation_value = 1 if elapsed % 2000 < 500 else 0
    _write_operation(operation_value, force=force)

    color = None
    if not _active.get(STATUS_SYSTEM_ERROR):
        color = _event_color(now)
    if color is None:
        color = _persistent_color(now)
    _write_rgb(color, force=force)


def current_status():
    """Return the highest-priority persistent status for diagnostics."""
    for state in _PERSISTENT_STATES:
        if _active.get(state):
            return state
    return STATUS_NORMAL
