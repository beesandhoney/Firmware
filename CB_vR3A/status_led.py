import utime

Pin = None
neopixel = None


STATUS_LED_PIN = 21
STATUS_LED_COUNT = 1
CONNECTING_BLINK_MS = 2000

COLOR_OFF = (0, 0, 0)
COLOR_CONNECTING = (0, 0, 40)
COLOR_CONNECTED = (0, 40, 0)

MODE_OFF = "off"
MODE_CONNECTING = "connecting"
MODE_CONNECTED = "connected"

_np = None
_mode = MODE_OFF
_last_color = None
_disabled = False
_last_write_ms = 0
MIN_WRITE_INTERVAL_MS = 500


def _disable(reason=None):
    global _disabled, _np
    _disabled = True
    _np = None
    if reason:
        print("Status LED disabled:", reason)


def _ensure_pixel():
    global _np, Pin, neopixel
    if _disabled:
        return None
    if _np is not None:
        return _np
    if Pin is None or neopixel is None:
        try:
            from machine import Pin as pin_mod
            import neopixel as neopixel_mod
            Pin = pin_mod
            neopixel = neopixel_mod
        except Exception as e:
            _disable(e)
            return None
    try:
        _np = neopixel.NeoPixel(Pin(STATUS_LED_PIN, Pin.OUT), STATUS_LED_COUNT)
    except Exception as e:
        _disable(e)
    return _np


def _write(color):
    global _last_color, _last_write_ms
    if _disabled:
        return
    if color == _last_color:
        return
    if color == COLOR_OFF and _np is None and _last_color is None:
        return
    now = utime.ticks_ms()
    if _last_color is not None and utime.ticks_diff(now, _last_write_ms) < MIN_WRITE_INTERVAL_MS:
        return
    pixel = _ensure_pixel()
    if pixel is None:
        return
    try:
        pixel[0] = color
        pixel.write()
        _last_color = color
        _last_write_ms = now
    except Exception as e:
        _disable(e)


def off():
    global _mode
    _mode = MODE_OFF
    _write(COLOR_OFF)


def set_connecting():
    global _mode
    _mode = MODE_CONNECTING
    tick(force=True)


def set_connected():
    global _mode
    _mode = MODE_CONNECTED
    _write(COLOR_CONNECTED)


def tick(force=False):
    if _mode == MODE_CONNECTED:
        _write(COLOR_CONNECTED)
        return
    if _mode == MODE_OFF:
        _write(COLOR_OFF)
        return
    if _mode != MODE_CONNECTING:
        return

    phase = utime.ticks_ms() % CONNECTING_BLINK_MS
    color = COLOR_CONNECTING if phase < (CONNECTING_BLINK_MS // 2) else COLOR_OFF
    if force or color != _last_color:
        _write(color)
