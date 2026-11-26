import ujson
from local_time import DEFAULT_TIMEZONE, normalize_timezone, valid_schedule_time, validate_lighting


class WaterCalibration:
    SENSOR_TYPE = "ultrasonic_distance_cm"
    DEFAULT_MAX_VOLUME_L = 10.0
    DEFAULT_POINT_COUNT = 4
    MIN_POINT_COUNT = 4
    MAX_POINT_COUNT = 4
    MIN_MAX_VOLUME_L = 0.1
    MAX_MAX_VOLUME_L = 10000.0

    max_volume_l = DEFAULT_MAX_VOLUME_L
    point_count = DEFAULT_POINT_COUNT
    points = [
        {"raw_value": None, "volume_l": 0.0},
        {"raw_value": None, "volume_l": 3.333},
        {"raw_value": None, "volume_l": 6.667},
        {"raw_value": None, "volume_l": 10.0},
    ]

    @classmethod
    def _target_volumes(cls, max_volume_l, point_count):
        step = max_volume_l / (point_count - 1)
        return [round(step * idx, 3) for idx in range(point_count)]

    @classmethod
    def configure(cls, max_volume_l, point_count=DEFAULT_POINT_COUNT):
        """Create a fresh guided-calibration table."""
        try:
            max_volume_l = float(max_volume_l)
            point_count = int(point_count)
        except Exception as e:
            print("Invalid volume calibration configuration:", e)
            return False
        if not cls.MIN_MAX_VOLUME_L <= max_volume_l <= cls.MAX_MAX_VOLUME_L:
            print("Maximum volume out of range:", max_volume_l)
            return False
        if not cls.MIN_POINT_COUNT <= point_count <= cls.MAX_POINT_COUNT:
            print("Calibration point count out of range:", point_count)
            return False

        cls.max_volume_l = round(max_volume_l, 3)
        cls.point_count = point_count
        cls.points = [
            {"raw_value": None, "volume_l": volume}
            for volume in cls._target_volumes(cls.max_volume_l, cls.point_count)
        ]
        return True

    @classmethod
    def update_points(cls, points, max_volume_l=None, point_count=None):
        """Load and sanitize new or legacy calibration data."""
        if not isinstance(points, list):
            return False
        if point_count is None:
            point_count = len(points)
        if max_volume_l is None:
            try:
                last = points[-1]
                max_volume_l = last.get("volume_l", last.get("liters"))
            except Exception:
                max_volume_l = None
        if max_volume_l is None:
            max_volume_l = cls.DEFAULT_MAX_VOLUME_L
        if not cls.configure(max_volume_l, point_count):
            return False

        cleaned = []
        targets = cls._target_volumes(cls.max_volume_l, cls.point_count)
        for idx in range(cls.point_count):
            try:
                p = points[idx]
                volume = p.get("volume_l", p.get("liters", targets[idx]))
                raw_value = p.get("raw_value", p.get("raw_depth_mm"))
                volume = round(float(volume), 3)
                raw_value = float(raw_value) if raw_value is not None else None
                cleaned.append({"raw_value": raw_value, "volume_l": volume})
            except Exception as e:
                print("Invalid calibration point at index", idx, e)
                cleaned.append({"raw_value": None, "volume_l": targets[idx]})
        if len(cleaned) == cls.point_count:
            cls.points = cleaned
            return True
        return False

    @classmethod
    def update_point(cls, idx, raw_value):
        """Store the sensor value for one guided target volume."""
        if not 0 <= idx < cls.point_count:
            print("update_point: invalid idx", idx)
            return False
        try:
            raw_value = float(raw_value)
        except Exception as e:
            print("update_point: invalid data", e)
            return False
        if not 2 <= raw_value <= 400:
            return False
        for other_idx, point in enumerate(cls.points):
            other = point["raw_value"]
            if other is not None and ((other_idx < idx and other <= raw_value) or
                                      (other_idx > idx and other >= raw_value)):
                return False
        cls.points[idx] = {
            "raw_value": raw_value,
            "volume_l": cls.points[idx]["volume_l"],
        }
        print("WATER CALIBRATION: point {}, target={:.3f} L, distance={:.2f} cm".format(
            idx + 1,
            cls.points[idx]["volume_l"],
            raw_value,
        ))
        return True

    @classmethod
    def to_serializable(cls):
        return [dict(point) for point in cls.points]

    @classmethod
    def status(cls):
        raw_values = [
            point.get("raw_value")
            for point in cls.points
            if point.get("raw_value") is not None
        ]
        recorded = len(raw_values)
        if recorded != cls.point_count:
            return {"state": "incomplete", "recorded": recorded, "required": cls.point_count}
        valid = raw_to_liters(raw_values[0]) is not None
        return {
            "state": "complete" if valid else "invalid",
            "recorded": recorded,
            "required": cls.point_count,
        }


def _load_water_calibration(data):
    """Load only ultrasonic distance calibration; reset incompatible legacy data."""
    if not isinstance(data, dict):
        return False

    max_volume_l = data.get("water_max_volume_l", WaterCalibration.DEFAULT_MAX_VOLUME_L)
    if data.get("water_sensor_type") != WaterCalibration.SENSOR_TYPE:
        # Preserve the configured tank capacity, but never interpret old
        # pressure ADC counts as distances.
        WaterCalibration.configure(max_volume_l, WaterCalibration.DEFAULT_POINT_COUNT)
        if "water_calibration" in data:
            print("Ignoring incompatible legacy water calibration")
        return False

    if "water_calibration" not in data:
        WaterCalibration.configure(max_volume_l, WaterCalibration.DEFAULT_POINT_COUNT)
        return False

    return WaterCalibration.update_points(
        data["water_calibration"],
        max_volume_l,
        data.get("water_calibration_point_count", WaterCalibration.DEFAULT_POINT_COUNT),
    )


class AmbientLightSettings:
    # Ambient light defaults (units: ms or raw ADC counts unless noted)
    defaults = {
        "als_enabled": True,
        "als_sample_interval_ms": 1000,
        "als_filter_window": 5,
        "als_threshold_on": 1200,
        "als_threshold_dim": 2000,
        "als_control_mode": "DIM",  # DIM or OFF
        "als_dim_level": 20,        # LED intensity percent (0-100)
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
    # Highest measured EC calibration reference (3977 uS/cm).
    MAX_EC_MS = 3.977
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
        'morning': 35,
        'daylight': 45,
        'evening': 35,
        'off': 0,

        'timezone': DEFAULT_TIMEZONE,

        # schedule (local HH:MM)
        't_morning_start': '07:30',
        't_day_start': '09:00',
        't_evening_start': '18:00',
        't_lights_off': '22:00',
    }

    @classmethod
    def update_values(cls, **kwargs):
        for key, value in kwargs.items():
            if key in cls.settings:
                if key.startswith('t_') and not valid_schedule_time(value):
                    continue
                if key == 'timezone':
                    value = normalize_timezone(value)
                cls.settings[key] = int(value) if key in ("morning", "daylight", "evening", "off") else value

    @classmethod
    def save_settings_to_file(cls, filename='settings.json'):
        return save_all_settings_to_file(filename)

    @classmethod
    def load_settings_from_file(cls, filename='settings.json'):
        try:
            with open(filename, 'r') as f:
                data = ujson.load(f)
            _load_lighting(data)
            _load_water_calibration(data)
            return True
        except OSError:
            return False


def _load_lighting(data):
    candidate = dict(LightIntensities.settings)
    candidate.update({k: data[k] for k in candidate if k in data})
    candidate['timezone'] = normalize_timezone(candidate['timezone'])
    candidate['off'] = 0
    # Migrate the former ON/OFF UI's coincident daytime boundaries.
    if candidate['t_morning_start'] == candidate['t_day_start'] == candidate['t_evening_start']:
        try:
            start = candidate['t_morning_start']
            end = candidate['t_lights_off']
            start = int(start[:2]) * 60 + int(start[3:])
            end = int(end[:2]) * 60 + int(end[3:])
            duration = (end - start) % 1440
            for i, key in ((1, 't_day_start'), (2, 't_evening_start')):
                minute = (start + duration * i // 3) % 1440
                candidate[key] = '%02d:%02d' % (minute // 60, minute % 60)
        except (TypeError, ValueError):
            pass
    try:
        validate_lighting(candidate)
    except (TypeError, ValueError) as error:
        print('Ignoring invalid saved lighting:', error)
        return
    LightIntensities.settings.update(candidate)


def _build_settings_payload():
    """Return combined settings dict for persistence or API."""
    payload = dict(LightIntensities.settings)
    payload["water_sensor_type"] = WaterCalibration.SENSOR_TYPE
    payload["water_max_volume_l"] = WaterCalibration.max_volume_l
    payload["water_calibration_point_count"] = WaterCalibration.point_count
    payload["water_calibration"] = WaterCalibration.to_serializable()
    payload["ambient"] = dict(AmbientLightSettings.settings)
    payload["ec"] = dict(ECSettings.settings)
    return payload


def save_all_settings_to_file(filename='settings.json'):
    try:
        import os
        temporary = filename + '.tmp'
        payload = _build_settings_payload()
        with open(temporary, 'w') as f:
            ujson.dump(payload, f)
            f.flush()
        with open(temporary) as f:
            if ujson.load(f) != payload:
                raise ValueError('Settings write verification failed')
        if hasattr(os, 'sync'):
            os.sync()
        os.rename(temporary, filename)
        try:
            import status_led
            status_led.settings_saved()
        except Exception as e:
            print("Settings-saved LED event failed:", e)
        return True
    except Exception as e:
        print("Failed to save settings to file:", e)
        return False


def load_all_settings_from_file(filename='settings.json'):
    """Load both light and water calibration settings, keeping backward compatibility."""
    try:
        with open(filename, 'r') as f:
            data = ujson.load(f)
    except (OSError, ValueError):
        return False

    if isinstance(data, dict):
        # Backward compatibility: flat structure
        _load_lighting(data)
        _load_water_calibration(data)
        if "ambient" in data and isinstance(data["ambient"], dict):
            AmbientLightSettings.update_values(**data["ambient"])
        if "ec" in data and isinstance(data["ec"], dict):
            ECSettings.update_values(**data["ec"])
    return True


def raw_to_liters(raw_value, points=None):
    """Convert ultrasonic cm using the factory configuration."""
    import hardware_config
    if hardware_config.installed() is not True:
        return None
    import water_config
    return water_config.calculate(raw_value)['volume_l']


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


def raw_to_depth_mm(distance_cm):
    """Convert ultrasonic cm into calibrated water height."""
    import hardware_config
    if hardware_config.installed() is not True:
        return None
    import water_config
    return water_config.calculate(distance_cm)['depth_mm']
