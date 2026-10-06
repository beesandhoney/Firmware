"""Shared pressure calibration and live reading API for both HTTP servers."""
from shared_settings import WaterCalibration, raw_to_liters, save_all_settings_to_file


def _param(params, key, default=None):
    return params.get(key, params.get(key.encode(), default))


def _reply(message, status=b"200 OK"):
    return message.encode(), b"HTTP/1.1 " + status + b"\r\n"


def _save(previous):
    if save_all_settings_to_file():
        return _reply("Calibration saved")
    WaterCalibration.max_volume_l, WaterCalibration.point_count, WaterCalibration.points = previous
    return _reply("Unable to save calibration", b"500 Internal Server Error")


def _snapshot():
    return (WaterCalibration.max_volume_l, WaterCalibration.point_count,
            WaterCalibration.to_serializable())


def configure(params):
    previous = _snapshot()
    if not WaterCalibration.configure(_param(params, "max_volume_l"), _param(params, "point_count", 4)):
        return _reply("Enter a capacity from 0.1 to 10000 L and four points", b"400 Bad Request")
    return _save(previous)


def record(params):
    try:
        index = int(_param(params, "point"))
    except (TypeError, ValueError):
        return _reply("Invalid calibration point", b"400 Bad Request")
    if not 0 <= index < 4:
        return _reply("Invalid calibration point", b"400 Bad Request")
    from firmware_loader import load
    load("gpio")
    from gpio import read_water_raw
    raw = read_water_raw(20, 5)
    if raw is None:
        return _reply("Unable to read pressure sensor", b"503 Service Unavailable")
    previous = _snapshot()
    if not WaterCalibration.update_point(index, raw):
        return _reply("ADC must increase at each higher volume. Check the fill level and sensor.", b"400 Bad Request")
    return _save(previous)


def read_live():
    from firmware_loader import load
    load("gpio")
    from gpio import read_water_raw, update_water_level_indicator
    from water_level import raw_to_depth_mm
    raw = read_water_raw(20, 5)
    volume = raw_to_liters(raw)
    led_count = update_water_level_indicator(volume)
    state = WaterCalibration.status()["state"]
    return {
        "enabled": True,
        "sensor_type": WaterCalibration.SENSOR_TYPE,
        "volume_l": volume,
        "led_count": led_count,
        "max_volume_l": WaterCalibration.max_volume_l,
        "depth_mm": raw_to_depth_mm(raw),
        "raw": raw,
        "calibration_state": state,
        "message": ("Unable to read pressure sensor" if raw is None else
                    "Live reading" if state == "complete" else
                    "Complete all four calibration points to display volume and light the LED bar"),
    }
