import ujson


class WaterCalibration:
    # Fixed depth marks; do not let users change these
    DEPTH_POINTS = [0, 25, 50, 100, 250]
    # Each point: {"depth_mm": fixed depth, "liters": float, "raw_depth_mm": raw touch reading}
    default_points = [
        {"depth_mm": DEPTH_POINTS[0], "liters": 0.0, "raw_depth_mm": None},
        {"depth_mm": DEPTH_POINTS[1], "liters": 0.5, "raw_depth_mm": None},
        {"depth_mm": DEPTH_POINTS[2], "liters": 1.0, "raw_depth_mm": None},
        {"depth_mm": DEPTH_POINTS[3], "liters": 1.5, "raw_depth_mm": None},
        {"depth_mm": DEPTH_POINTS[4], "liters": 2.0, "raw_depth_mm": None},
    ]
    points = list(default_points)

    @classmethod
    def update_points(cls, points):
        """Replace calibration points with sanitized input (used on file load)."""
        cleaned = []
        for idx, depth_ref in enumerate(cls.DEPTH_POINTS):
            try:
                p = points[idx]
                liters = round(float(p.get("liters", 0)), 1)
                raw_depth = p.get("raw_depth_mm", None)
                raw_depth = float(raw_depth) if raw_depth is not None else None
                cleaned.append({"depth_mm": depth_ref, "liters": liters, "raw_depth_mm": raw_depth})
            except Exception as e:
                print("Invalid calibration point at index", idx, e)
                cleaned.append(cls.default_points[idx])
        if len(cleaned) == 5:
            cls.points = cleaned

    @classmethod
    def update_point(cls, idx, liters, raw_depth_mm=None):
        """Update a single calibration point by fixed index."""
        if not 0 <= idx < 5:
            print("update_point: invalid idx", idx)
            return
        try:
            liters = round(float(liters), 1)
            raw_depth_mm = float(raw_depth_mm) if raw_depth_mm is not None else None
        except Exception as e:
            print("update_point: invalid data", e)
            return

        # Ensure list length and fixed depths
        if len(cls.points) != 5:
            cls.points = list(cls.default_points)

        depth_ref = cls.DEPTH_POINTS[idx]
        cls.points[idx] = {"depth_mm": depth_ref, "liters": liters, "raw_depth_mm": raw_depth_mm}

    @classmethod
    def to_serializable(cls):
        return cls.points


class AmbientLightSettings:
    # Ambient light defaults (units: ms or raw ADC counts unless noted)
    defaults = {
        "als_enabled": True,
        "als_sample_interval_ms": 1000,
        "als_filter_window": 5,
        "als_threshold_on": 1200,
        "als_threshold_dim": 2000,
        "als_control_mode": "DIM",  # DIM or OFF
        "als_dim_level": 20,        # PWM duty value (0-1023 typical)
        "als_ramp_rate_ms": 200,
        "als_fault_action": "normal",  # "normal" or "dim"
    }
    settings = dict(defaults)

    @classmethod
    def update_values(cls, **kwargs):
        for key, value in kwargs.items():
            if key not in cls.defaults:
                continue
            try:
                if key in ("als_enabled",):
                    cls.settings[key] = bool(int(value)) if isinstance(value, (str, bytes)) else bool(value)
                elif key == "als_control_mode":
                    val = value if isinstance(value, str) else value.decode()
                    cls.settings[key] = val.upper()
                elif key == "als_fault_action":
                    val = value if isinstance(value, str) else value.decode()
                    cls.settings[key] = val.lower()
                else:
                    cls.settings[key] = int(value)
            except Exception as e:
                print("Ambient settings ignored invalid value for", key, e)

    @classmethod
    def to_serializable(cls):
        return dict(cls.settings)


class ECSettings:
    defaults = {
        "ec_low_alarm_us": 700,
        "ec_high_alarm_us": 2200,
        "plant_category": "herbs",
        "plant_stage": "default",
    }
    settings = dict(defaults)

    @classmethod
    def update_values(cls, **kwargs):
        for key, value in kwargs.items():
            if key not in cls.defaults:
                continue
            try:
                if key in ("ec_low_alarm_us", "ec_high_alarm_us"):
                    cls.settings[key] = int(value)
                else:
                    cls.settings[key] = value if isinstance(value, str) else value.decode()
            except Exception as e:
                print("EC settings ignored invalid value for", key, e)

    @classmethod
    def to_serializable(cls):
        return dict(cls.settings)

class LightIntensities:
    # Default settings
    settings = {
        # intensities (%)
        'morning': 350,
        'daylight': 450,
        'evening': 350,
        'off': 0,

        # schedule (HH:MM)
        't_morning_start': '07:30',
        't_day_start': '09:00',
        't_evening_start': '18:00',
        't_lights_off': '22:00',
    }

    @classmethod
    def update_values(cls, **kwargs):
        for key, value in kwargs.items():
            if key in cls.settings:
                cls.settings[key] = value

    @classmethod
    def save_settings_to_file(cls, filename='settings.json'):
        try:
            with open(filename, 'w') as f:
                ujson.dump(_build_settings_payload(), f)
            return True
        except Exception as e:
            print("Failed to save settings to file:", e)
            return False

    @classmethod
    def load_settings_from_file(cls, filename='settings.json'):
        try:
            with open(filename, 'r') as f:
                data = ujson.load(f)
            # only update known keys
            for k in cls.settings:
                if k in data:
                    cls.settings[k] = data[k]
            if "water_calibration" in data:
                WaterCalibration.update_points(data["water_calibration"])
            return True
        except OSError:
            return False


def _build_settings_payload():
    """Return combined settings dict for persistence or API."""
    payload = dict(LightIntensities.settings)
    payload["water_calibration"] = WaterCalibration.to_serializable()
    payload["ambient"] = dict(AmbientLightSettings.settings)
    payload["ec"] = dict(ECSettings.settings)
    return payload


def save_all_settings_to_file(filename='settings.json'):
    try:
        with open(filename, 'w') as f:
            ujson.dump(_build_settings_payload(), f)
        return True
    except Exception as e:
        print("Failed to save settings to file:", e)
        return False


def load_all_settings_from_file(filename='settings.json'):
    """Load both light and water calibration settings, keeping backward compatibility."""
    try:
        with open(filename, 'r') as f:
            data = ujson.load(f)
    except OSError:
        return False

    if isinstance(data, dict):
        # Backward compatibility: flat structure
        for k in LightIntensities.settings:
            if k in data:
                LightIntensities.settings[k] = data[k]
        if "water_calibration" in data:
            WaterCalibration.update_points(data["water_calibration"])
        if "ambient" in data and isinstance(data["ambient"], dict):
            AmbientLightSettings.update_values(**data["ambient"])
        if "ec" in data and isinstance(data["ec"], dict):
            ECSettings.update_values(**data["ec"])
    return True


def depth_to_liters(depth_mm, points=None):
    """Linearly interpolate liters from depth using calibration points."""
    if depth_mm is None:
        return None

    pts = points or WaterCalibration.points
    try:
        pts = sorted(pts, key=lambda p: p["depth_mm"])
    except Exception as e:
        print("Invalid calibration points:", e)
        return None

    if not pts:
        return None

    if depth_mm <= pts[0]["depth_mm"]:
        return pts[0]["liters"]
    if depth_mm >= pts[-1]["depth_mm"]:
        return pts[-1]["liters"]

    for i in range(len(pts) - 1):
        d0, l0 = pts[i]["depth_mm"], pts[i]["liters"]
        d1, l1 = pts[i + 1]["depth_mm"], pts[i + 1]["liters"]
        if d0 <= depth_mm <= d1 or d1 <= depth_mm <= d0:
            # Avoid division by zero
            if d1 == d0:
                return l0
            ratio = (depth_mm - d0) / (d1 - d0)
            return round(l0 + ratio * (l1 - l0), 2)

    return None


# Runtime ambient-light status (used for diagnostics / UI)
ambient_status = {
    "raw": None,
    "filtered": None,
    "mode": "UNKNOWN",
    "fault": False,
}


def update_ambient_status(raw=None, filtered=None, mode=None, fault=False):
    """Store the latest ambient light diagnostics for other modules to read."""
    try:
        if raw is not None:
            ambient_status["raw"] = raw
        if filtered is not None:
            ambient_status["filtered"] = filtered
        if mode is not None:
            ambient_status["mode"] = mode
        ambient_status["fault"] = bool(fault)
    except Exception as e:
        print("Failed to update ambient status:", e)
    return ambient_status
