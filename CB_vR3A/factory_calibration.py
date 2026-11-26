"""REPL-only factory setup: import factory_calibration; factory_calibration.start()."""
import water_config as config

SAMPLE_COUNT = 30
MIN_VALID_FRACTION = 0.8
STABILITY_MM = 10.0  # Maximum peak-to-peak spread; validate on the product.
VERIFICATION_TOLERANCE_MM = 10.0
DEBUG_INTERVAL_MS = 1000


def _check_config_api():
    """Catch an old source or boot-loaded bytecode module before measuring."""
    if getattr(config, 'FORMAT_VERSION', None) == 2 and callable(getattr(config, 'create', None)):
        return True
    print('Calibration cannot start: water_config firmware is out of date or still cached.')
    print('Loaded module:', getattr(config, '__file__', 'water_config'))
    print('Upload factory_calibration.py, water_config.py, volume_http.py,')
    print('_fw_water_config.mpy and _fw_volume_http.mpy together.')
    print('Startup loads _fw_water_config.mpy; uploading water_config.py alone is insufficient.')
    print('Then press Ctrl-D to restart, Ctrl-C to stop the application, and start calibration again.')
    return False


def measure(sensor):
    readings = []
    for _ in range(SAMPLE_COUNT):
        value = sensor.read_once_cm()
        if value is not None and 2 <= value <= 400:
            readings.append(value * 10)
    if len(readings) < SAMPLE_COUNT * MIN_VALID_FRACTION:
        raise ValueError('Too few valid ultrasonic measurements: %d/%d' % (len(readings), SAMPLE_COUNT))
    readings.sort()
    if readings[-1] - readings[0] > STABILITY_MM:
        raise ValueError('Unstable ultrasonic measurements: min=%.1f max=%.1f spread=%.1f mm (limit=%.1f mm)' %
                         (readings[0], readings[-1], readings[-1] - readings[0], STABILITY_MM))
    middle = len(readings) // 2
    return readings[middle] if len(readings) % 2 else (readings[middle-1] + readings[middle])/2


def _debug(sensor, calibration, interval_ms, samples, csv=False):
    import utime
    interval_ms = max(60, int(interval_ms))
    if samples is not None:
        samples = int(samples)
        if samples < 1:
            raise ValueError('samples must be positive, or None for continuous output')
    window = []
    previous = None
    count = 0
    started = utime.ticks_ms()
    print('Single ultrasonic readings; Ctrl-C stops debug. No calibration is saved.')
    print('Distance is sensor-to-water; height is calibrated and unclamped ("--" without calibration).')
    print('Delta is from the previous valid distance. Statistics cover the last %d attempts.' % SAMPLE_COUNT)
    print('Calibration spread limit: %.1f mm; minimum valid samples: %.0f%%.' %
          (STABILITY_MM, MIN_VALID_FRACTION * 100))
    if csv:
        print('sample,time_ms,distance_mm,height_mm,delta_mm,median_mm,min_mm,max_mm,spread_mm,invalid_window,status')
    try:
        while samples is None or count < samples:
            distance_cm = sensor.read_once_cm()
            distance = distance_cm * 10 if distance_cm is not None and 2 <= distance_cm <= 400 else None
            window.append(distance)
            if len(window) > SAMPLE_COUNT:
                window.pop(0)
            valid = sorted(value for value in window if value is not None)
            delta = distance - previous if distance is not None and previous is not None else None
            if distance is not None:
                previous = distance
            height = None
            status = 'invalid_echo'
            if distance is not None:
                if calibration is None:
                    status = 'uncalibrated'
                else:
                    reading = config.calculate(distance_cm, calibration)
                    height = reading.get('unclamped_height_mm')
                    status = reading['status']
            median = low = high = spread = None
            if valid:
                middle = len(valid) // 2
                median = valid[middle] if len(valid) % 2 else (valid[middle-1] + valid[middle]) / 2
                low, high = valid[0], valid[-1]
                spread = high - low
            values = (distance, height, delta, median, low, high, spread)
            formatted = ['--' if value is None else '%.1f' % value for value in values]
            count += 1
            elapsed = utime.ticks_diff(utime.ticks_ms(), started)
            invalid = len(window) - len(valid)
            if csv:
                print('%d,%d,%s,%d/%d,%s' %
                      (count, elapsed, ','.join(formatted), invalid, len(window), status))
            else:
                print('[%d | %.1f s] Distance: %s mm | Height: %s mm | Change: %s mm' %
                      (count, elapsed / 1000, formatted[0], formatted[1], formatted[2]))
                print('  Last %d readings (mm): min %s | max %s | median %s | spread %s' %
                      (len(window), formatted[4], formatted[5], formatted[3], formatted[6]))
                print('  Invalid echoes: %d/%d | Status: %s\n' % (invalid, len(window), status))
            if samples is None or count < samples:
                utime.sleep_ms(interval_ms)
    except KeyboardInterrupt:
        pass
    print('Water debug stopped.')


def debug(interval_ms=DEBUG_INTERVAL_MS, samples=None, csv=False):
    """Print labelled readings once a second; csv=True enables compact data logging."""
    import hardware_config
    if hardware_config.installed() is not True:
        print('Water level debug unavailable: hardware not installed or configuration invalid.')
        return
    from firmware_loader import load
    _debug(load('water_level').WaterLevelSensor(), config.current(), interval_ms, samples, csv)


def show(calibration):
    """Show reference conditions and derived geometry before accepting or saving."""
    if calibration is None:
        print('No valid version-2 calibration. Perform empty/full calibration.')
        return
    config.validate(calibration)
    w = calibration['water_level']
    print('Empty distance (0%%): %.1f mm' % w['distance_empty_mm'])
    print('Full distance (100%%): %.1f mm' % w['distance_full_mm'])
    print('Bottom / full diameter: %.1f / %.1f mm' % (w['bottom_diameter_mm'], w['full_diameter_mm']))
    print('Calculated effective height: %.1f mm' % w['effective_height_mm'])
    print('Maximum recommended volume: %.3f L' % w['max_volume_liters'])
    print('Calibrated:', w['calibrated'])


def capture_point(sensor, point_number):
    """Capture empty or recommended-full distance; retry without losing the diameter."""
    point = None
    while True:
        try:
            print('Point 1 (0%): ensure the reservoir is EMPTY.' if point_number == 1 else
                  'Point 2 (100%): fill to the MAXIMUM RECOMMENDED operating level.')
            if point is None:
                prompt = ('Internal bottom diameter (mm): ' if point_number == 1 else
                          'Internal diameter at maximum recommended water level (mm): ')
                diameter = config.number(input(prompt))
                if diameter <= 0:
                    raise ValueError('Diameter must be positive')
                point = dict(diameter_mm=diameter)
            input('Let the surface settle; press Enter to measure point %d: ' % point_number)
            point['distance_mm'] = measure(sensor)
            print('Point %d accepted: diameter=%.1f mm, sensor distance=%.1f mm' %
                  (point_number, point['diameter_mm'], point['distance_mm']))
            return point
        except (ValueError, OSError, TypeError) as error:
            print('Point %d rejected: %s' % (point_number, error))
            while True:
                action = input('Retry this point [Enter/r], edit diameter [e], or cancel [c]: ').strip().lower()
                if action in ('', 'r', 'e', 'c'):
                    break
            if action == 'c':
                return None
            if action == 'e':
                point = None


def calibrate(sensor):
    """Keep the accepted reference while capturing or redoing the other one."""
    if not _check_config_api():
        return None
    points = [None, None]
    while True:
        for index in range(2):
            if points[index] is None:
                points[index] = capture_point(sensor, index + 1)
                if points[index] is None:
                    print('Calibration cancelled; previous configuration retained.')
                    return None
        empty, full = points
        candidate = None
        try:
            candidate = config.create(empty['diameter_mm'], full['diameter_mm'],
                                      empty['distance_mm'], full['distance_mm'])
            show(candidate)
        except (ValueError, TypeError, OverflowError) as error:
            candidate = None
            print('Calibration rejected:', error)
        while True:
            prompt = ('Keep for verification/saving [y], redo point [1/2], or cancel [n]: ' if candidate else
                      'Redo point [1/2], or cancel [n]: ')
            action = input(prompt).strip().lower()
            if action in ('1', '2', 'n', 'c') or (candidate and action == 'y'):
                break
        if action == 'y':
            return candidate
        if action in ('n', 'c'):
            print('Calibration cancelled; previous configuration retained.')
            return None
        points[int(action) - 1] = None


def verify(sensor, calibration):
    """Optional independent check; never changes calibration data."""
    config.validate(calibration)
    reference = input('Known intermediate water height (mm), or Enter for live measurement only: ').strip()
    height = None
    if reference:
        height = config.number(reference)
        if not 0 <= height <= calibration['water_level']['effective_height_mm']:
            raise ValueError('Reference height outside calibrated range')
    distance = measure(sensor)
    result = config.calculate(distance / 10, calibration)
    print('Distance: %.1f mm | Height: %s mm | Volume: %s L | Fill: %s%% | Status: %s' %
          (distance, result['depth_mm'], result['volume_l'], result['fill_percent'], result['status']))
    if not result['valid']:
        print('FAIL: measurement is invalid or outside the calibrated range.')
        return False
    if height is None:
        return None
    error = abs(result['unclamped_height_mm'] - height)
    passed = error <= VERIFICATION_TOLERANCE_MM
    print('%s: height error %.1f mm (limit %.1f mm).' %
          ('PASS' if passed else 'FAIL', error, VERIFICATION_TOLERANCE_MM))
    return passed


def _water_menu():
    if not _check_config_api():
        return
    from firmware_loader import load
    sensor = load('water_level').WaterLevelSensor()
    # Deep copy: cancelled edits never alter the active configuration.
    saved = config.current()
    draft = config.json.loads(config.json.dumps(saved)) if saved else None
    verification_failed = False
    unsaved = False
    print('Factory water calibration v2: empty (0%) / maximum recommended level (100%).')
    print('Stop the application with Ctrl-C before starting. No container height entry is needed.')
    try:
        while True:
            print('1 New calibration  2 Show parameters  3 Verify measurement  4 Save  5 Exit  6 Debug readings')
            choice = input('Select: ').strip()
            try:
                if choice == '1':
                    candidate = calibrate(sensor)
                    if candidate is not None:
                        draft = candidate
                        verification_failed = False
                        unsaved = True
                        print('Calibration is pending, NOT saved. Choose 4 Save to keep it after restart.')
                elif choice == '2':
                    print('Stored:')
                    show(config.current())
                    print('Pending:')
                    show(draft)
                elif choice == '3':
                    if draft is None:
                        raise ValueError('Calibrate first')
                    # A failed attempted verification must not be silently saved.
                    previous_failure = verification_failed
                    verification_failed = True
                    result = verify(sensor, draft)
                    verification_failed = previous_failure if result is None else not result
                elif choice == '4':
                    if draft is None:
                        raise ValueError('Calibrate first')
                    if verification_failed:
                        raise ValueError('Verification failed; repeat it successfully or redo calibration before saving')
                    show(draft)
                    print('An independent check at an intermediate water level is recommended (menu 3).')
                    if input('Save this factory calibration? [y/N]: ').strip().lower() == 'y':
                        config.save(draft)
                        unsaved = False
                        print('Factory configuration saved.')
                elif choice == '5':
                    if unsaved:
                        print('Unsaved calibration discarded; stored calibration unchanged.')
                    break
                elif choice == '6':
                    print('Using pending calibration.' if draft else 'Using stored calibration, if available.')
                    _debug(sensor, draft if draft else config.current(), DEBUG_INTERVAL_MS, None)
            except (ValueError, OSError, TypeError, KeyError, OverflowError) as error:
                print('Rejected:', error)
    except (KeyboardInterrupt, EOFError):
        print('Cancelled; unsaved changes discarded.')
    print('Factory setup closed. Use Ctrl-D to restart normal operation.')


def _hardware_menu():
    import hardware_config
    print('HARDWARE CONFIGURATION\n----------------------')
    print('Water level sensor installed?\n\n1. Yes\n2. No')
    choice = input('Selection (Enter to cancel): ').strip()
    if choice not in ('1', '2'):
        print('Cancelled; previous configuration retained.')
        return
    installed = choice == '1'
    print('Water level sensor installed:', installed)
    if input('Save this hardware configuration? [y/N]: ').strip().lower() == 'y':
        hardware_config.save(dict(version=1, hardware=dict(water_level_sensor_installed=installed)))
        print('Hardware configuration saved. Restart with Ctrl-D after factory setup.')
    else:
        print('Cancelled; previous configuration retained.')


def _show_factory():
    import hardware_config
    installed = hardware_config.installed()
    print('Water level sensor installed:', installed)
    if installed is None:
        print(hardware_config.fault())
        print('Factory acceptance: FAIL')
    elif not installed:
        print('Water level calibration: Not applicable')
    else:
        ready = hardware_config.measurement_ready()
        print('Water level calibration:', 'Completed' if ready else 'Required')
        print('Calibration acceptance:', 'PASS; verify sensor operation before release' if ready else 'FAIL')
        show(config.current())


def start():
    print('Stop the application with Ctrl-C before factory setup.')
    try:
        while True:
            print('GROW - FACTORY CONFIGURATION\n----------------------------')
            print('1. Hardware configuration\n2. Water level calibration\n3. View factory configuration\n4. Exit')
            choice = input('Selection: ').strip()
            try:
                if choice == '1':
                    _hardware_menu()
                    _show_factory()
                elif choice == '2':
                    import hardware_config
                    if hardware_config.installed() is True:
                        _water_menu()
                    elif hardware_config.installed() is False:
                        print('Water level calibration: Not applicable')
                    else:
                        print(hardware_config.fault())
                elif choice == '3':
                    _show_factory()
                elif choice == '4':
                    break
            except (ValueError, OSError, TypeError, KeyError) as error:
                print('Rejected:', error)
    except (KeyboardInterrupt, EOFError):
        print('Cancelled; unsaved changes discarded.')
    print('Factory setup closed. Use Ctrl-D to restart normal operation.')
