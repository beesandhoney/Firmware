"""Factory-only hardware capabilities (SW-SPEC-HW-001)."""
try:
    import ujson as json
except ImportError:
    import json
import os

CONFIG_FILE = 'factory_hardware.json'
_config = None
_loaded = False
_error = None


def validate(value):
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported hardware configuration version')
    if type(value['hardware']['water_level_sensor_installed']) is not bool:
        raise ValueError('water_level_sensor_installed must be Boolean')
    return value


def load():
    global _config, _loaded, _error
    try:
        with open(CONFIG_FILE) as stream:
            _config = validate(json.load(stream))
        _error = None
    except (OSError, ValueError, KeyError, TypeError) as error:
        _config = None
        _error = 'Factory hardware configuration missing or invalid: %s' % error
        print('FACTORY configuration fault:', _error)
    _loaded = True
    return _config


def current():
    return _config if _loaded else load()


def installed():
    value = current()
    return value['hardware']['water_level_sensor_installed'] if value else None


def fault():
    current()
    return _error


def save(value):
    global _config, _loaded, _error
    validate(value)
    temporary = CONFIG_FILE + '.tmp'
    with open(temporary, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
    with open(temporary) as stream:
        committed = validate(json.load(stream))
    if hasattr(os, 'sync'):
        os.sync()
    os.rename(temporary, CONFIG_FILE)
    _config, _loaded, _error = committed, True, None


def measurement_ready():
    if installed() is not True:
        return False
    import water_config
    return water_config.calculate(None)['status'] != 'configuration_fault'
