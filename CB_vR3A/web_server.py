from microdot import Microdot, send_file, Response
import ujson
from shared_settings import (
    LightIntensities,
    AmbientLightSettings,
    ECSettings,
    WaterCalibration,
    save_all_settings_to_file,
    load_all_settings_from_file,
    raw_to_liters,
    ambient_status as ambient_runtime_status,
)
from gpio import setup_adc, read_ec_value, read_water_raw, setup_ambient_adc, read_ambient_raw
import _thread
import network
import time
import uasyncio as asyncio  # currently unused, but kept in case you add async later
import traceback
import uselect as select

from captive_dns import DNSServer

app = Microdot()
_exit_settings_callback = None



# Track whether HTTP server has already been started
_server_started = False
_server_thread_running = False

# Track captive DNS server so connecting devices get auto-redirected
_dns_poller = None
_dns_server = None
_dns_thread_running = False


# Form keys
INT_KEYS = {'morning', 'daylight', 'evening', 'off'}
TIME_KEYS = {'t_morning_start', 't_day_start', 't_evening_start', 't_lights_off'}
ALS_INT_KEYS = {
    'als_sample_interval_ms',
    'als_filter_window',
    'als_threshold_on',
    'als_threshold_dim',
    'als_dim_level',
    'als_ramp_rate_ms',
}
ALS_BOOL_KEYS = {'als_enabled'}
ALS_STR_KEYS = {'als_control_mode', 'als_fault_action'}
EC_INT_KEYS = {'ec_low_alarm_us', 'ec_high_alarm_us'}
EC_STR_KEYS = {'plant_category', 'plant_stage'}


# ---------- Routes ----------

@app.route('/')
def index(request):
    # Serve the settings page
    return send_file('settings.html')


@app.route('/generate_204')
@app.route('/gen_204')
@app.route('/hotspot-detect.html')
@app.route('/ncsi.txt')
@app.route('/redirect')
def captive_redirect(request):
    # Redirect common captive portal probes back to the settings UI
    return Response('', status_code=302, headers={'Location': '/'})


@app.errorhandler(404)
def not_found(request):
    # Any unknown path should still land on settings
    return Response('', status_code=302, headers={'Location': '/'})


@app.route('/update_settings', methods=['POST'])
def update_settings(request):
    print("inside update_settings")
    formData = request.form
    new_settings = {}
    new_ambient = {}
    new_ec = {}

    try:
        # Parse form data
        for key, value in formData.items():
            if isinstance(value, list):
                value = value[0]

            if key in INT_KEYS:
                try:
                    new_settings[key] = int(value)
                except ValueError:
                    return Response(
                        "Invalid integer for {}: {}".format(key, value),
                        status_code=400
                    )
            elif key in TIME_KEYS:
                # simple sanity check: "HH:MM"
                if len(value) == 5 and value[2] == ':':
                    new_settings[key] = value
                else:
                    return Response(
                        "Invalid time for {}: {}".format(key, value),
                        status_code=400
                    )
            elif key in ALS_INT_KEYS:
                try:
                    new_ambient[key] = int(value)
                except ValueError:
                    return Response(
                        "Invalid integer for {}: {}".format(key, value),
                        status_code=400
                    )
            elif key in ALS_BOOL_KEYS:
                new_ambient[key] = 1 if str(value).lower() in ("1", "true", "on") else 0
            elif key in ALS_STR_KEYS:
                new_ambient[key] = value
            elif key in EC_INT_KEYS:
                try:
                    new_ec[key] = int(value)
                except ValueError:
                    return Response(
                        "Invalid integer for {}: {}".format(key, value),
                        status_code=400
                    )
            elif key in EC_STR_KEYS:
                new_ec[key] = value
            else:
                # Unknown key – ignore or log
                print("Ignoring unknown form key:", key)

        print("Before updating light intensities", new_settings)
        LightIntensities.update_values(**new_settings)
        ton = new_ambient.get("als_threshold_on", AmbientLightSettings.settings["als_threshold_on"])
        tdim = new_ambient.get("als_threshold_dim", AmbientLightSettings.settings["als_threshold_dim"])
        if ton >= tdim:
            return Response("T_ON must be less than T_DIM/OFF", status_code=400)
        if "als_control_mode" in new_ambient:
            mode_val = str(new_ambient["als_control_mode"]).upper()
            if mode_val not in ("DIM", "OFF"):
                return Response("Invalid control mode", status_code=400)
            new_ambient["als_control_mode"] = mode_val
        if "als_fault_action" in new_ambient:
            fault_val = str(new_ambient["als_fault_action"]).lower()
            if fault_val not in ("normal", "dim"):
                return Response("Invalid fault action", status_code=400)
            new_ambient["als_fault_action"] = fault_val
        AmbientLightSettings.update_values(**new_ambient)

        if "ec_low_alarm_us" in new_ec and "ec_high_alarm_us" in new_ec:
            if new_ec["ec_low_alarm_us"] >= new_ec["ec_high_alarm_us"]:
                return Response("EC low must be less than high limit", status_code=400)
        ECSettings.update_values(**new_ec)

        if save_all_settings_to_file():
            print("Updated light settings:", LightIntensities.settings)
            print("Updated water calibration:", WaterCalibration.points)

            # optional: exit settings mode (if main app uses this callback)
            if _exit_settings_callback is not None:
                _exit_settings_callback()

            # Simple success page with fast redirect back to settings
            content = """\
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Settings saved</title>
  <meta http-equiv="refresh" content="2;url=/">
</head>
<body>
  <h2>Settings saved</h2>
  <p>Device is applying changes and returning to the main program. You can close this page.</p>
</body>
</html>
"""
            return Response(content, headers={'Content-Type': 'text/html'})
        else:
            raise Exception("Failed to save settings to file")

    except Exception as e:
        print("Failed to update settings:", e)
        traceback.print_exc()
        return Response('Failed to update settings', status_code=500)


@app.route('/adc_value')
def adc_value(request):
    adc = setup_adc()
    voltage = read_ec_value(adc)
    headers = {'Content-Type': 'text/plain'}
    return Response(str(voltage), headers=headers)


@app.route('/current_settings')
def current_settings(request):
    """Return the latest light settings without forcing a form submit."""
    headers = {'Content-Type': 'application/json'}
    try:
        payload = dict(LightIntensities.settings)
        payload["water_max_volume_l"] = WaterCalibration.max_volume_l
        payload["water_calibration_point_count"] = WaterCalibration.point_count
        payload["water_calibration"] = WaterCalibration.to_serializable()
        payload["ambient"] = AmbientLightSettings.to_serializable()
        payload["ec"] = ECSettings.to_serializable()
        return Response(ujson.dumps(payload), headers=headers)
    except Exception as e:
        print("Failed to read settings:", e)
        return Response(ujson.dumps({'error': 'unable to read settings'}), status_code=500, headers=headers)


@app.route('/configure_water_calibration')
def configure_water_calibration(request):
    max_volume_l = request.args.get('max_volume_l')
    point_count = request.args.get('point_count')
    if not WaterCalibration.configure(max_volume_l, point_count):
        return Response('Invalid maximum volume or point count', status_code=400)
    if save_all_settings_to_file():
        return Response('Calibration procedure configured')
    return Response('Failed to save calibration configuration', status_code=500)


@app.route('/update_cal_point')
def update_cal_point(request):
    try:
        idx = int(request.args.get('point')) - 1
    except Exception:
        return Response('Invalid calibration point', status_code=400)
    raw_value = read_water_raw()
    if raw_value is None:
        return Response('Unable to read water sensor', status_code=503)
    if not WaterCalibration.update_point(idx, raw_value):
        return Response('Calibration point out of range', status_code=400)
    if save_all_settings_to_file():
        return Response('Calibration point recorded')
    return Response('Failed to save calibration point', status_code=500)


@app.route('/water_level')
def water_level(request):
    """Return current volume directly from the touch sensor."""
    headers = {'Content-Type': 'application/json'}
    try:
        raw = read_water_raw()
        volume_l = raw_to_liters(raw)
        return Response(ujson.dumps({'volume_l': volume_l, 'raw': raw}), headers=headers)
    except Exception as e:
        print("Failed to read water level:", e)
        return Response(ujson.dumps({'error': 'unable to read water level'}), status_code=500, headers=headers)


@app.route('/ambient_status')
def ambient_status(request):
    """Return latest ambient light readings (raw + filtered + mode)."""
    headers = {'Content-Type': 'application/json'}
    try:
        status = dict(ambient_runtime_status)
        if status.get("raw") is None:
            try:
                adc = setup_ambient_adc()
                raw = read_ambient_raw(adc)
                status["raw"] = raw
                status["filtered"] = raw
                status["mode"] = "UNKNOWN"
            except Exception as e:
                print("ambient_status live read failed:", e)
        return Response(ujson.dumps(status), headers=headers)
    except Exception as e:
        print("Failed to read ambient status:", e)
        return Response(ujson.dumps({'error': 'unable to read ambient status'}), status_code=500, headers=headers)


# ---------- Helper functions for settings.json (optional) ----------

def load_settings():
    try:
        with open('settings.json', 'r') as f:
            return ujson.load(f)
    except OSError:
        return None


def save_settings(settings):
    try:
        with open('settings.json', 'w') as f:
            f.write(ujson.dumps(settings))
        return True
    except OSError:
        return False


def set_exit_settings_callback(callback):
    global _exit_settings_callback
    _exit_settings_callback = callback


# ---------- Captive DNS control ----------

def _dns_loop():
    global _dns_thread_running
    while _dns_thread_running:
        try:
            for response in _dns_poller.ipoll(1000):
                sock, event, *others = response
                _dns_server.handle(sock, event, others)
        except Exception as e:
            # Don't let DNS failures crash the settings server
            print("DNS loop error:", e)


def start_captive_dns(ap_ip):
    """Start a lightweight DNS server that forces all lookups to the AP IP."""
    global _dns_poller, _dns_server, _dns_thread_running

    if _dns_server is not None:
        print("DNS already running; skip start")
        return

    try:
        _dns_poller = select.poll()
        _dns_server = DNSServer(_dns_poller, ap_ip)
        _dns_thread_running = True
        _thread.start_new_thread(_dns_loop, ())
        print("Captive DNS started on", ap_ip)
    except Exception as e:
        print("Failed to start captive DNS:", e)
        _dns_server = None
        _dns_thread_running = False


def stop_captive_dns():
    global _dns_poller, _dns_server, _dns_thread_running
    _dns_thread_running = False
    if _dns_server:
        try:
            _dns_server.stop(_dns_poller)
        except Exception as e:
            print("Error stopping DNS server:", e)
    _dns_poller = None
    _dns_server = None


# ---------- Web server control ----------

# ---------- Web server control ----------

def start_web_server():
    """Start the settings web server (blocking).

    IMPORTANT:
    - Does NOT touch AP/STA state.
    - Assume AP/STA has been set up by main.py / CaptivePortal.
    - Runs in the main thread and blocks until reset.
    """
    # Make sure settings (including calibration) are loaded before serving
    load_all_settings_from_file()

    print("Starting web server on 0.0.0.0:80 (blocking)")
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        ap = network.WLAN(network.AP_IF)
        print("Web server init: STA active?", sta.active(), "isconnected?", getattr(sta, "isconnected", lambda: None)())
        print("Web server init: AP active?", ap.active())
        try:
            print("Web server init: AP ifconfig", ap.ifconfig())
        except Exception as e:
            print("Web server init: AP ifconfig failed:", e)
    except Exception as e:
        print("Web server init: network status probe failed:", e)
    try:
        app.run(host='0.0.0.0', port=80)
    except KeyboardInterrupt:
        print("Server stopped by user")


if __name__ == '__main__':
    try:
        start_web_server()
    except KeyboardInterrupt:
        print("Server stopped by user")
