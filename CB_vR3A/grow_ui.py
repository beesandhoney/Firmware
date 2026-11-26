"""Consumer settings and status shared by both local HTTP servers."""
import math
import sys
import utime
from local_time import TIMEZONES, valid_schedule_time, schedule_phase, status as clock_status, LIGHT_PHASES, validate_lighting
from shared_settings import LightIntensities, AmbientLightSettings, ECSettings, save_all_settings_to_file

LOW_WATER_PERCENT = 20
ICON_FILES = ('moon', 'shade', 'sunrise', 'brightness', 'plant-test-tube', 'weather-temperature', 'sunset')


def settings():
    light = LightIntensities.settings
    return dict(lighting={name: dict(start=light[key], brightness_percent=light[level])
                          for name, key, level in LIGHT_PHASES}, timezone=light['timezone'], timezones=TIMEZONES,
                lights_on=light['t_morning_start'], lights_off=light['t_lights_off'],
                ambient_enabled=AmbientLightSettings.settings['als_enabled'],
                ec_alarm_low=ECSettings.settings['ec_low_alarm_us'] / 1000,
                ec_alarm_high=ECSettings.settings['ec_high_alarm_us'] / 1000,
                ec_max_ms=ECSettings.MAX_EC_MS,
                ec_low_ms=ECSettings.settings['ec_low_alarm_us'] / 1000,
                ec_high_ms=ECSettings.settings['ec_high_alarm_us'] / 1000)


def save_settings(params):
    """Validate the complete form before applying, roll back RAM if persistence fails."""
    data = {}
    for key, value in params.items():
        key = key.decode() if isinstance(key, bytes) else key
        data[key] = value.decode() if isinstance(value, bytes) else value
    # Accept the previous API names for already deployed clients. Internal storage
    # remains in uS/cm so existing saved settings and physical alarms stay intact.
    for canonical, legacy in (('ec_alarm_low', 'ec_low_ms'), ('ec_alarm_high', 'ec_high_ms')):
        if canonical in data:
            data[legacy] = data[canonical]
    required = ('timezone', 'ambient_enabled', 'ec_low_ms', 'ec_high_ms')
    if any(key not in data for key in required):
        raise ValueError('Please complete all settings.')
    if data['timezone'] not in TIMEZONES:
        raise ValueError('Select a supported time zone.')
    lighting = dict(LightIntensities.settings)
    if any(name + '_start' in data for name, _, _ in LIGHT_PHASES):
        for name, key, level in LIGHT_PHASES:
            lighting[key] = data.get(name + '_start')
            value = data.get(name + '_brightness', '0' if name == 'night' else '')
            try:
                if isinstance(value, bool) or str(int(value)) != str(value):
                    raise ValueError()
                lighting[level] = int(value)
            except (TypeError, ValueError):
                raise ValueError('Brightness must be a whole percentage from 0 to 100.')
    else:
        # Older deployed clients submit an ON/OFF interval. Divide it into
        # three equal phases without changing their constant daytime output.
        if not all(valid_schedule_time(data.get(k)) for k in ('lights_on', 'lights_off')):
            raise ValueError('Enter times in 24-hour HH:MM format.')
        start = _minutes(data['lights_on'])
        duration = (_minutes(data['lights_off']) - start) % 1440
        if duration < 3:
            raise ValueError('The light interval must allow three distinct phases (at least 3 minutes).')
        for i, (_, key, level) in enumerate(LIGHT_PHASES):
            minute = (start + duration * i // 3) % 1440
            lighting[key] = '%02d:%02d' % (minute // 60, minute % 60)
            lighting[level] = 0 if i == 3 else (lighting.get('daylight', 45) or 45)
    validate_lighting(lighting)
    if data['ambient_enabled'] not in ('1', '0', True, False):
        raise ValueError('Choose Enabled or Disabled for ambient light sensing.')
    try:
        low, high = float(data['ec_low_ms']), float(data['ec_high_ms'])
    except (ValueError, TypeError):
        raise ValueError('Enter valid nutrient alert values.')
    if not (math.isfinite(low) and math.isfinite(high) and 0 <= low < high <= ECSettings.MAX_EC_MS):
        raise ValueError('High EC alert must be greater than Low EC alert; both must be within 0–%s mS/cm.' % ECSettings.MAX_EC_MS)
    low_us, high_us = round(low * 1000), round(high * 1000)
    if low_us >= high_us:
        raise ValueError('Nutrient alerts must differ by at least 0.001 mS/cm.')
    previous = (dict(LightIntensities.settings), dict(AmbientLightSettings.settings), dict(ECSettings.settings))
    lighting['timezone'] = data['timezone']
    LightIntensities.settings.update(lighting)
    AmbientLightSettings.settings['als_enabled'] = data['ambient_enabled'] in ('1', True)
    ECSettings.settings.update(ec_low_alarm_us=low_us, ec_high_alarm_us=high_us)
    if not save_all_settings_to_file():
        LightIntensities.settings, AmbientLightSettings.settings, ECSettings.settings = previous
        raise OSError('Could not save settings. Your previous settings are unchanged.')
    return dict(message='Settings saved successfully.', settings=settings())


def _minutes(value):
    return int(value[:2]) * 60 + int(value[3:])


def next_switch(light, minute):
    """Next on/off transition of the saved schedule, in local wall-clock time."""
    now_on = light[schedule_phase(light, minute)] > 0
    boundaries = sorted(set(_minutes(light[k]) for k in
                            ('t_morning_start', 't_day_start', 't_evening_start', 't_lights_off')),
                        key=lambda value: (value - minute) % 1440 or 1440)
    for boundary in boundaries:
        if (light[schedule_phase(light, boundary)] > 0) != now_on:
            return dict(time='%02d:%02d' % (boundary // 60, boundary % 60), on=not now_on)
    return None


def lighting_status(clock):
    import gpio
    application = sys.modules.get('app_main')
    running = bool(application and getattr(application, 'runtime_initialized', False))
    if application and not running and hasattr(application, 'service_portal_lighting'):
        application.portal_lighting_active = True
        application.service_portal_lighting()
    active = running or bool(application and getattr(application, 'portal_lighting_active', False))
    manual = bool(application and getattr(application, 'state', None) == getattr(application, 'COSYMODE', -1))
    output = getattr(gpio, 'last_value', None)
    on = output > 0 if output is not None else (False if not active else None)
    transition = None
    if clock.get('local_time') and not manual:
        transition = next_switch(LightIntensities.settings, _minutes(clock['local_time'][11:16]))
    phase = next_phase = next_transition = None
    if clock.get('local_time') and active and not manual:
        minute = _minutes(clock['local_time'][11:16])
        internal = schedule_phase(LightIntensities.settings, minute)
        for i, (name, key, level) in enumerate(LIGHT_PHASES):
            if internal == level:
                phase = name
                next_phase, next_key, _ = LIGHT_PHASES[(i + 1) % 4]
                next_transition = LightIntensities.settings[next_key]
                break
    brightness = round(output * 100 / 1023) if _finite(output) and 0 <= output <= 1023 else None
    return dict(phase=phase, brightness_percent=brightness, next_phase=next_phase,
                next_transition=next_transition, on=on, mode='manual' if manual else 'scheduled', next_switch=transition,
                override_ends=getattr(application, 'MANUAL_OVERRIDE_ENDS', None) if manual else None,
                clock_valid=bool(clock.get('local_time')), active=active,
                ambient_adjusted=bool(active and AmbientLightSettings.settings['als_enabled'] and
                                      getattr(application, 'ambient_controller', None) and
                                      application.ambient_controller.mode in ('OFF', 'DIMMED', 'DIMMED (fault)')))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def status(read_ec=None, read_water=None):
    """Independent sensor failures never suppress the other cards."""
    from firmware_loader import load
    load('gpio')
    application = sys.modules.get('app_main')
    clock = clock_status(LightIntensities.settings['timezone'])
    import hardware_config
    capability = hardware_config.installed()
    payload = dict(clock=clock, ambient_enabled=AmbientLightSettings.settings['als_enabled'],
                   capabilities=dict(water_level=capability), factory_configuration_error=hardware_config.fault())
    try:
        payload['lighting'] = lighting_status(clock)
    except Exception as error:
        print('Lighting status unavailable:', error)
        payload['lighting'] = dict(on=None, mode='scheduled', clock_valid=False, active=False)
    fan = getattr(sys.modules.get('gpio'), 'fan_pwm_percent', None)
    payload['fan'] = dict(pwm_percent=fan if _finite(fan) and 0 <= fan <= 100 else None)
    value = None
    try:
        if application and getattr(application, 'runtime_initialized', False):
            stamp = getattr(application, 'current_ec_ms', None)
            if stamp is not None and utime.ticks_diff(utime.ticks_ms(), stamp) <= 30000:
                value = application.current_ec_value
        elif read_ec:
            value = read_ec()
        if not _finite(value) or value < 0:
            value = None
    except Exception as error:
        print('Nutrient reading unavailable:', error)
    low, high = ECSettings.settings['ec_low_alarm_us'], ECSettings.settings['ec_high_alarm_us']
    state = 'unavailable' if value is None else 'low' if value < low else 'high' if value > high else 'optimal'
    payload['nutrients'] = dict(value_ms=None if value is None else value / 1000, status=state,
                                state={'low': 'LOW', 'optimal': 'NORMAL', 'high': 'HIGH', 'unavailable': 'FAULT'}[state])
    try:
        if read_water is None:
            from volume_http import read_live
            read_water = read_live
        water = read_water() if capability is True else dict(valid=False, status='disabled' if capability is False else 'configuration_fault')
        valid = water.get('valid') and _finite(water.get('fill_percent')) and _finite(water.get('volume_l'))
        payload['water'] = dict(fill_percent=water['fill_percent'] if valid else None,
                                volume_l=water['volume_l'] if valid else None,
                                status=('low' if water['fill_percent'] < LOW_WATER_PERCENT else 'good') if valid else
                                'out_of_range' if water.get('status') == 'out_of_range' else 'unavailable')
    except Exception as error:
        print('Water status unavailable:', error)
        payload['water'] = dict(fill_percent=None, volume_l=None, status='unavailable')
    payload['water']['state'] = 'DISABLED' if capability is False else ('ACTIVE' if payload['water']['status'] in ('good', 'low') else 'FAULT')
    if capability is False:
        payload['water']['status'] = 'disabled'
    value = None
    try:
        value = load('temperature').read_celsius()
        if not _finite(value):
            value = None
    except Exception as error:
        print('Temperature unavailable:', error)
    payload['temperature'] = dict(value_c=value, status='valid' if value is not None else 'unavailable')
    return payload
