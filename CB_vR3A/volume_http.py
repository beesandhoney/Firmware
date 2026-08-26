"""Volume calibration HTTP actions, loaded only after the portal is running."""


def configure(params):
    from shared_settings import WaterCalibration, save_all_settings_to_file

    maximum = params.get(b"max_volume_l")
    count = params.get(b"point_count")
    if maximum in (None, b"") or count in (None, b""):
        return b"Maximum volume and point count are required", b"HTTP/1.1 400 Bad Request\r\n"
    if not WaterCalibration.configure(maximum, count):
        return b"Invalid maximum volume or point count", b"HTTP/1.1 400 Bad Request\r\n"
    if save_all_settings_to_file():
        return b"Calibration procedure configured", b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
    return b"Failed to save calibration configuration", b"HTTP/1.1 500 Internal Server Error\r\n"


def record(params):
    from gpio import read_water_raw
    from shared_settings import WaterCalibration, save_all_settings_to_file

    try:
        idx = int(params.get(b"point")) - 1
    except Exception:
        return b"Invalid calibration point", b"HTTP/1.1 400 Bad Request\r\n"
    if not 0 <= idx < WaterCalibration.point_count:
        return b"Calibration point out of range", b"HTTP/1.1 400 Bad Request\r\n"
    raw = read_water_raw(6, 8)
    if raw is None:
        return b"Unable to read water sensor", b"HTTP/1.1 503 Service Unavailable\r\n"
    if not WaterCalibration.update_point(idx, raw):
        return b"Unable to store calibration point", b"HTTP/1.1 400 Bad Request\r\n"
    if save_all_settings_to_file():
        return b"Calibration point recorded", b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
    return b"Failed to save calibration", b"HTTP/1.1 500 Internal Server Error\r\n"


def read_live():
    from gpio import read_water_raw
    from shared_settings import WaterCalibration, raw_to_liters

    raw = read_water_raw(4, 5)
    calibration = WaterCalibration.status()
    return {
        "volume_l": raw_to_liters(raw),
        "raw": raw,
        "calibration_state": calibration["state"],
        "calibrated_points": calibration["recorded"],
        "required_points": calibration["required"],
    }
