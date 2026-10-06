"""UTC-to-local conversion for the supported modern European time zones.

Current EU/UK rules: last Sunday of March/October at 01:00 UTC.
No RTC writes, host timezone assumptions, or fixed Unix epoch are required.
"""
import time

DEFAULT_TIMEZONE = 'Europe/Stockholm'
TIMEZONES = ('Europe/Stockholm', 'Europe/Lisbon', 'Europe/London')


def normalize_timezone(zone):
    return zone if zone in TIMEZONES else DEFAULT_TIMEZONE


def offset_seconds(utc, zone):
    zone = normalize_timezone(zone)
    month, day, hour, weekday = utc[1], utc[2], utc[3], utc[6]
    # March and October both have 31 days. weekday is Monday=0.
    last_sunday = 31 - ((weekday + 31 - day + 1) % 7)
    after_transition = day > last_sunday or (day == last_sunday and hour >= 1)
    dst = (3 < month < 10 or (month == 3 and after_transition) or
           (month == 10 and not after_transition))
    return (3600 if zone == 'Europe/Stockholm' else 0) + (3600 if dst else 0)


def localtime(zone=DEFAULT_TIMEZONE, timestamp=None):
    if timestamp is None:
        timestamp = time.time()
    utc = time.gmtime(timestamp)
    return time.gmtime(timestamp + offset_seconds(utc, zone))


def status(zone=DEFAULT_TIMEZONE):
    zone = normalize_timezone(zone)
    try:
        timestamp = time.time()
        if time.gmtime(timestamp)[0] < 2024:
            return {'timezone': zone, 'local_time': None}
        current = localtime(zone, timestamp)
        return {'timezone': zone, 'local_time': '%04d-%02d-%02d %02d:%02d:%02d' % current[:6]}
    except (ValueError, OverflowError):
        return {'timezone': zone, 'local_time': None}


def valid_schedule_time(value):
    try:
        return (len(value) == 5 and value[2] == ':' and
                value[:2].isdigit() and value[3:].isdigit() and
                0 <= int(value[:2]) < 24 and 0 <= int(value[3:]) < 60)
    except (TypeError, AttributeError, ValueError):
        return False


def schedule_phase(settings, minute):
    """Most recent daily phase boundary, including schedules crossing midnight."""
    phases = (('t_morning_start', 'morning'), ('t_day_start', 'daylight'),
              ('t_evening_start', 'evening'), ('t_lights_off', 'off'))
    selected, nearest = 'off', 1441
    for key, phase in phases:
        hour, minutes = settings[key].split(':')
        elapsed = (minute - (int(hour) * 60 + int(minutes))) % 1440
        if elapsed <= nearest:
            selected, nearest = phase, elapsed
    return selected
