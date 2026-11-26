"""Read-only water status shared by the application and HTTP servers."""
import water_config
import hardware_config

STALE_TIMEOUT_MS = 10000
_last_valid = None
_last_valid_ms = None
_last_config = None


def read_live():
    import utime
    global _last_valid, _last_valid_ms, _last_config
    installed = hardware_config.installed()
    if installed is not True or not hardware_config.measurement_ready():
        _last_valid = _last_valid_ms = _last_config = None
        disabled = installed is False
        error = hardware_config.fault() if installed is None else (
            None if disabled else water_config.calculate(None)['configuration_error'])
        return dict(enabled=installed, state='DISABLED' if disabled else 'FAULT',
                    status='disabled' if disabled else 'configuration_fault', valid=False,
                    sensor_type='ultrasonic', raw=None, distance_cm=None, volume_l=None,
                    depth_mm=None, fill_percent=None, max_volume_l=None, led_count=0,
                    calibration_state='not_applicable' if disabled else 'fault',
                    configuration_error=error,
                    message='Not applicable' if disabled else error)
    from firmware_loader import load
    load('gpio')
    from gpio import read_water_raw, update_water_level_indicator
    config = water_config.current()
    if config is not _last_config:
        _last_valid = _last_valid_ms = None
        _last_config = config
    raw = read_water_raw(3, 60)
    result = water_config.calculate(raw)
    now = utime.ticks_ms()
    if result['valid']:
        _last_valid, _last_valid_ms = dict(result), now
    elif result['status'] == 'unavailable' and _last_valid is not None:
        if utime.ticks_diff(now, _last_valid_ms) < STALE_TIMEOUT_MS:
            result = dict(_last_valid)
            result.update(status='stale', valid=False)
    result.update(enabled=True, sensor_type='ultrasonic', raw=raw, distance_cm=raw,
                  calibration_state='fault' if result['status'] == 'configuration_fault' else 'complete')
    result['message'] = {'valid': 'Live reading', 'stale': 'Last valid reading — sensor unavailable',
                         'unavailable': 'Water level unavailable', 'out_of_range': 'Water level outside container range',
                         'configuration_fault': 'Water level unavailable — factory configuration required'}[result['status']]
    result['state'] = 'ACTIVE' if result['valid'] else 'FAULT'
    result['led_count'] = update_water_level_indicator(result['volume_l'] if result['valid'] else None)
    return result
