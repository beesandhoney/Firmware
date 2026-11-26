"""SW-SPEC-WL-001 rev 1.1: empty/full factory geometry (mm, litres)."""
try:
    import ujson as json
except ImportError:
    import json
import math
import os

CONFIG_FILE = 'factory_water.json'
FORMAT_VERSION = 2
# Engineering defaults; set product-specific limits after validation.
MIN_DISTANCE_MM, MAX_DISTANCE_MM = 20.0, 4000.0
MIN_EFFECTIVE_HEIGHT_MM, MAX_EFFECTIVE_HEIGHT_MM = 50.0, 3980.0
MIN_VOLUME_LITERS, MAX_VOLUME_LITERS = 0.1, 10000.0
# Allow only floating-point serialization rounding, not measurement error.
DERIVED_REL_TOLERANCE = 0.00001
_config = None
_loaded = False
_load_error = None


def number(value):
    if isinstance(value, bool):
        raise ValueError('Expected a number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return value


def volume(water, height):
    """Volume below height in a straight-sided frustum, converted to litres."""
    bottom = water['bottom_diameter_mm']
    full = water['full_diameter_mm']
    diameter = bottom + (full - bottom) * height / water['effective_height_mm']
    return math.pi * height * (bottom*bottom + bottom*diameter + diameter*diameter) / 12000000


def create(bottom_diameter_mm, full_diameter_mm, distance_empty_mm, distance_full_mm):
    bottom, full, empty, filled = [number(v) for v in
                                 (bottom_diameter_mm, full_diameter_mm, distance_empty_mm, distance_full_mm)]
    if min(bottom, full) <= 0:
        raise ValueError('Both diameters must be positive')
    if not (MIN_DISTANCE_MM <= empty <= MAX_DISTANCE_MM and
            MIN_DISTANCE_MM <= filled <= MAX_DISTANCE_MM):
        raise ValueError('Reference distances outside ultrasonic range')
    if filled >= empty:
        raise ValueError('Full distance must be smaller than empty distance')
    height = empty - filled
    if not MIN_EFFECTIVE_HEIGHT_MM < height <= MAX_EFFECTIVE_HEIGHT_MM:
        raise ValueError('Effective height %.1f mm must be > %.1f mm and <= %.1f mm' %
                         (height, MIN_EFFECTIVE_HEIGHT_MM, MAX_EFFECTIVE_HEIGHT_MM))
    water = dict(bottom_diameter_mm=bottom, full_diameter_mm=full,
                 distance_empty_mm=empty, distance_full_mm=filled,
                 effective_height_mm=height, calibrated=True)
    maximum = number(volume(water, height))
    if not MIN_VOLUME_LITERS <= maximum <= MAX_VOLUME_LITERS:
        raise ValueError('Volume %.3f L outside permitted %.3f-%.3f L range' %
                         (maximum, MIN_VOLUME_LITERS, MAX_VOLUME_LITERS))
    water['max_volume_liters'] = maximum
    return dict(version=FORMAT_VERSION, water_level=water)


def validate(config):
    """Validate stored values, including redundant derived height and volume."""
    if type(config['version']) is not int or config['version'] != FORMAT_VERSION:
        raise ValueError('Version 2 factory calibration required; recalibrate empty/full')
    w = config['water_level']
    keys = ('bottom_diameter_mm', 'full_diameter_mm', 'distance_empty_mm',
            'distance_full_mm', 'effective_height_mm', 'max_volume_liters')
    for key in keys:
        if type(w[key]) not in (int, float):
            raise ValueError('Factory measurements must be numeric')
        number(w[key])
    if type(w['calibrated']) is not bool:
        raise ValueError('Invalid calibrated flag')
    expected = create(w['bottom_diameter_mm'], w['full_diameter_mm'],
                      w['distance_empty_mm'], w['distance_full_mm'])['water_level']
    for key in ('effective_height_mm', 'max_volume_liters'):
        if abs(w[key] - expected[key]) > max(0.000001, expected[key] * DERIVED_REL_TOLERANCE):
            raise ValueError('Inconsistent stored ' + key)
    return config


def load():
    global _config, _loaded, _load_error
    try:
        with open(CONFIG_FILE) as f:
            _config = validate(json.load(f))
        _load_error = None
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        _config = None
        if isinstance(error, OSError) and error.args and error.args[0] == 2:
            _load_error = 'Calibration file missing: ' + CONFIG_FILE
        else:
            _load_error = 'Cannot load %s: %s' % (CONFIG_FILE, error)
        print('WATER calibration fault:', _load_error)
    _loaded = True
    return _config


def current():
    return _config if _loaded else load()


def save(config):
    global _config, _loaded, _load_error
    validate(config)
    temporary = CONFIG_FILE + '.tmp'
    with open(temporary, 'w') as f:
        json.dump(config, f)
        f.flush()
    with open(temporary) as f:
        committed = validate(json.load(f))
    if hasattr(os, 'sync'):
        os.sync()
    os.rename(temporary, CONFIG_FILE)
    _config, _loaded = committed, True
    _load_error = None


def calculate(distance_cm, config=None):
    config = current() if config is None else config
    result = dict(volume_l=None, depth_mm=None, fill_percent=None, max_volume_l=None,
                  status='configuration_fault', valid=False, configuration_error=None)
    if config is None:
        result['configuration_error'] = _load_error or 'No factory calibration loaded'
        return result
    try:
        validate(config)
        w = config['water_level']
        if not w['calibrated']:
            result['configuration_error'] = 'Stored calibration is marked uncalibrated'
            return result
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        result['configuration_error'] = str(error)
        return result
    # Recompute derived values from the measured references, after checking storage.
    height_max = w['distance_empty_mm'] - w['distance_full_mm']
    geometry = dict(w)
    geometry['effective_height_mm'] = height_max
    result['max_volume_l'] = volume(geometry, height_max)
    try:
        distance = number(distance_cm) * 10
        if not MIN_DISTANCE_MM <= distance <= MAX_DISTANCE_MM:
            raise ValueError('Distance outside sensor range')
    except (ValueError, TypeError, OverflowError):
        result['status'] = 'unavailable'
        return result
    height = w['distance_empty_mm'] - distance
    result['status'] = 'valid' if 0 <= height <= height_max else 'out_of_range'
    result['valid'] = result['status'] == 'valid'
    result['unclamped_height_mm'] = height
    result['depth_mm'] = max(0, min(height_max, height))
    result['volume_l'] = volume(geometry, result['depth_mm'])
    result['fill_percent'] = max(0, min(100, 100 * result['volume_l'] / result['max_volume_l']))
    return result
