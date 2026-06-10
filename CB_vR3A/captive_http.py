import uerrno
import uio
import usocket as socket
import ujson
import uos
import utime

from collections import namedtuple

ReqInfo = namedtuple("ReqInfo", ["type", "path", "params", "host"])

import gc

REQUEST_READ_TIMEOUT_S = 0.4
RESPONSE_CHUNK_SIZE = 536
HTTP_LISTEN_BACKLOG = 1
EC_SETTLE_MS = 200
WATER_LIVE_SAMPLES = 4
WATER_LIVE_DELAY_MS = 5
WATER_CAL_SAMPLES = 6
WATER_CAL_DELAY_MS = 8

LightIntensities = None
AmbientLightSettings = None
ECSettings = None
WaterCalibration = None
ambient_status = None
save_all_settings_to_file = None
load_all_settings_from_file = None
depth_to_liters = None
_settings_loaded = False
Creds = None

setup_adc = None
read_ec_value = None
set_ec_power = None
read_water_raw = None
water_raw_to_depth_mm = None
setup_ambient_adc = None
read_ambient_raw = None


def _ensure_settings_loaded():
    global LightIntensities, AmbientLightSettings, ECSettings, WaterCalibration
    global ambient_status, save_all_settings_to_file, load_all_settings_from_file
    global depth_to_liters, _settings_loaded

    if LightIntensities is None:
        from shared_settings import (
            LightIntensities as LightIntensities_mod,
            AmbientLightSettings as AmbientLightSettings_mod,
            ECSettings as ECSettings_mod,
            WaterCalibration as WaterCalibration_mod,
            ambient_status as ambient_status_mod,
            save_all_settings_to_file as save_all_settings_to_file_mod,
            load_all_settings_from_file as load_all_settings_from_file_mod,
            depth_to_liters as depth_to_liters_mod,
        )
        LightIntensities = LightIntensities_mod
        AmbientLightSettings = AmbientLightSettings_mod
        ECSettings = ECSettings_mod
        WaterCalibration = WaterCalibration_mod
        ambient_status = ambient_status_mod
        save_all_settings_to_file = save_all_settings_to_file_mod
        load_all_settings_from_file = load_all_settings_from_file_mod
        depth_to_liters = depth_to_liters_mod

    if not _settings_loaded:
        try:
            load_all_settings_from_file()
        except Exception as e:
            print("HTTP settings load failed:", e)
        _settings_loaded = True


def _ensure_gpio_loaded():
    global setup_adc, read_ec_value, set_ec_power, read_water_raw
    global water_raw_to_depth_mm, setup_ambient_adc, read_ambient_raw

    if setup_adc is None:
        from gpio import (
            setup_adc as setup_adc_mod,
            read_ec_value as read_ec_value_mod,
            set_ec_power as set_ec_power_mod,
            read_water_raw as read_water_raw_mod,
            water_raw_to_depth_mm as water_raw_to_depth_mm_mod,
            setup_ambient_adc as setup_ambient_adc_mod,
            read_ambient_raw as read_ambient_raw_mod,
        )
        setup_adc = setup_adc_mod
        read_ec_value = read_ec_value_mod
        set_ec_power = set_ec_power_mod
        read_water_raw = read_water_raw_mod
        water_raw_to_depth_mm = water_raw_to_depth_mm_mod
        setup_ambient_adc = setup_ambient_adc_mod
        read_ambient_raw = read_ambient_raw_mod


def _get_creds_class():
    global Creds
    if Creds is None:
        from credentials import Creds as Creds_mod
        Creds = Creds_mod
    return Creds


def unquote(string):
    """stripped down implementation of urllib.parse unquote_to_bytes"""

    if not string:
        return b''

    if isinstance(string, str):
        string = string.encode('utf-8')

    # Parse percent escapes, but tolerate malformed sequences by keeping them
    # literal so bad client input cannot crash the HTTP handler.
    hexdigits = b"0123456789abcdefABCDEF"
    out = bytearray()
    i = 0
    n = len(string)

    while i < n:
        ch = string[i]
        if ch == 37:  # b'%'
            if i + 2 < n:
                a = string[i + 1]
                b = string[i + 2]
                if (a in hexdigits) and (b in hexdigits):
                    out.append(int(string[i + 1:i + 3], 16))
                    i += 3
                    continue
            # Invalid or incomplete escape -> keep '%'
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1

    return bytes(out)


class HTTPServer:
    def __init__(self, poller, local_ip, mode="wifi", exit_callback=None, portal_status_getter=None):
        self.name = "HTTP Server"
        self.sock = None
        gc.collect()
        stage = "socket"
        try:
            self.sock = socket.socket()
            stage = "setsockopt"
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception as e:
                print("HTTP server SO_REUSEADDR skipped:", e)
            stage = "bind"
            self.sock.bind(("0.0.0.0", 80))
            stage = "listen"
            self.sock.listen(HTTP_LISTEN_BACKLOG)
            stage = "timeout"
            try:
                self.sock.settimeout(0.25)
            except Exception:
                self.sock.setblocking(False)
        except Exception as e:
            print("HTTP server socket init failed at {}: {}".format(stage, e))
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            raise
        print(self.name, "listening on", ("0.0.0.0", 80))

        if type(local_ip) is bytes:
            self.local_ip = local_ip
        else:
            self.local_ip = local_ip.encode()
        self.exit_callback = exit_callback
        self.portal_status_getter = portal_status_getter
        self.mode = mode
        self.retry_requested = False
        self.ec_adc = None
        self.ambient_adc = None
        if mode == "settings":
            self.routes = {
                b"/": b"./settings.html",
                b"/settings": b"./settings.html",
                b"/wifi": b"./index.html",
                b"/login": self.login,
                b"/retry_wifi": self.retry_wifi,
                b"/reset_wifi": self.reset_wifi,
                b"/update_settings": self.update_lights,  # backward compatibility
                b"/update_lights": self.update_lights,
                b"/update_cal_point": self.update_cal_point,
                b"/exit_settings": self.exit_settings,
                b"/current_settings": self.current_settings,
                b"/adc_value": self.adc_value,
                b"/water_level": self.water_level,
                b"/ambient_status": self.ambient_status,
                b"/live_status": self.live_status,
                b"/generate_204": self.redirect_root,
                b"/gen_204": self.redirect_root,
                b"/hotspot-detect.html": self.redirect_root,
                b"/ncsi.txt": self.redirect_root,
                b"/redirect": self.redirect_root,
            }
        elif mode == "sta":
            self.routes = {
                b"/": b"./settings.html",
                b"/settings": b"./settings.html",
                b"/wifi": b"./index.html",
                b"/login": self.login,
                b"/retry_wifi": self.retry_wifi,
                b"/reset_wifi": self.reset_wifi,
                b"/update_settings": self.update_lights,
                b"/update_lights": self.update_lights,
                b"/update_cal_point": self.update_cal_point,
                b"/exit_settings": self.exit_settings,
                b"/current_settings": self.current_settings,
                b"/adc_value": self.adc_value,
                b"/water_level": self.water_level,
                b"/ambient_status": self.ambient_status,
                b"/live_status": self.live_status,
                b"/generate_204": self.redirect_root,
                b"/gen_204": self.redirect_root,
                b"/hotspot-detect.html": self.redirect_root,
                b"/ncsi.txt": self.redirect_root,
                b"/redirect": self.redirect_root,
            }
        else:
            self.routes = {
                b"/": self.portal_home,
                b"/wifi": b"./index.html",
                b"/settings": b"./settings.html",
                b"/login": self.login,
                b"/retry_wifi": self.retry_wifi,
                b"/reset_wifi": self.reset_wifi,
                b"/update_settings": self.update_lights,
                b"/update_lights": self.update_lights,
                b"/update_cal_point": self.update_cal_point,
                b"/current_settings": self.current_settings,
                b"/adc_value": self.adc_value,
                b"/water_level": self.water_level,
                b"/ambient_status": self.ambient_status,
                b"/live_status": self.live_status,
                b"/generate_204": self.redirect_root,
                b"/gen_204": self.redirect_root,
                b"/hotspot-detect.html": self.redirect_root,
                b"/ncsi.txt": self.redirect_root,
                b"/redirect": self.redirect_root,
            }

        self.ssid = None

    def serve_once(self, idle_ms=250):
        client_sock = None
        try:
            client_sock, addr = self.sock.accept()
        except OSError as e:
            # Timeout/no pending client. Keep the setup portal alive.
            if idle_ms:
                utime.sleep_ms(idle_ms)
            return True

        try:
            self.handle_blocking(client_sock)
            return True
        except Exception as e:
            if not self.is_transient_socket_error(e):
                print("HTTP blocking handler failed:", e)
            return False
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            gc.collect()

    def handle_blocking(self, client_sock):
        try:
            client_sock.settimeout(REQUEST_READ_TIMEOUT_S)
        except Exception:
            pass

        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            try:
                chunk = client_sock.recv(512)
            except OSError as e:
                if self.is_transient_socket_error(e):
                    return
                raise
            if not chunk:
                break
            data += chunk

        if not data:
            return

        try:
            req = self.parse_request(data)
        except Exception as e:
            print("HTTP bad request:", e)
            return
        if not self.is_valid_req(req):
            headers = (
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                b"Location: http://%s/\r\n" % self.local_ip
            )
            body = uio.BytesIO(b"")
            headers = self._ensure_content_length(headers, b"")
        else:
            body, headers = self.get_response(req)

        self.write_blocking(client_sock, body, headers)

    def is_transient_socket_error(self, err):
        code = err.args[0] if getattr(err, "args", None) else err
        transient = (
            uerrno.EAGAIN,
            11,
            103,
            104,
            116,
        )
        for name in ("ETIMEDOUT", "ECONNABORTED", "ECONNRESET"):
            try:
                transient = transient + (getattr(uerrno, name),)
            except AttributeError:
                pass
        return code in transient

    def _ensure_connection_close(self, headers):
        lowered = headers.lower()
        if b"connection:" not in lowered:
            headers += b"Connection: close\r\n"
        return headers

    def _ensure_content_length(self, headers, body):
        if b"content-length:" not in headers.lower():
            headers += b"Content-Length: %d\r\n" % len(body)
        return headers

    def _file_headers(self, path):
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            b"Connection: close\r\n"
        )
        try:
            headers += b"Content-Length: %d\r\n" % uos.stat(path)[6]
        except Exception:
            pass
        return headers

    def _write_all_blocking(self, client_sock, data):
        if not data:
            return True
        view = memoryview(data)
        offset = 0
        total = len(data)
        while offset < total:
            try:
                written = client_sock.write(view[offset:])
            except OSError as e:
                if self.is_transient_socket_error(e):
                    return False
                raise
            if not written:
                return False
            offset += written
        return True

    def write_blocking(self, client_sock, body, headers):
        try:
            headers = self._ensure_connection_close(headers)
            if not self._write_all_blocking(client_sock, headers + b"\r\n"):
                return
            while True:
                chunk = body.read(RESPONSE_CHUNK_SIZE)
                if not chunk:
                    break
                if not self._write_all_blocking(client_sock, chunk):
                    return
        finally:
            try:
                body.close()
            except Exception:
                pass

    def set_ip(self, new_ip, new_ssid):
        """update settings after connected to local WiFi"""

        self.local_ip = new_ip.encode()
        self.ssid = new_ssid
        self.routes = {b"/": self.connected}

    def stop(self, poller=None):
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        print(self.name, "stopped")

    def parse_request(self, req):
        """parse a raw HTTP request to get items of interest"""

        req_lines = req.split(b"\r\n")
        req_type, full_path, http_ver = req_lines[0].split(b" ")
        path = full_path.split(b"?")
        base_path = path[0]
        query = path[1] if len(path) > 1 else None
        query_params = {}
        if query:
            for param in query.split(b"&"):
                if b"=" in param:
                    key, val = param.split(b"=", 1)
                else:
                    key, val = param, b""
                query_params[unquote(key)] = unquote(val)
        hosts = [line.split(b": ")[1] for line in req_lines if b"Host:" in line]
        host = hosts[0] if hosts else self.local_ip

        return ReqInfo(req_type, base_path, query_params, host)

    def login(self, params):
        ssid = unquote(params.get(b"ssid", None))
        password = unquote(params.get(b"password", None))

        # Write out credentials
        _get_creds_class()(ssid=ssid, password=password).write()
        # Always request an immediate retry after login, even if credentials
        # are unchanged from what's already stored.
        self.retry_requested = True

        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://%s/wifi\r\n" % self.local_ip
        )

        return b"", headers

    def retry_wifi(self, params):
        self.retry_requested = True
        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://%s/\r\n" % self.local_ip
        )
        return b"", headers

    def reset_wifi(self, params):
        _get_creds_class()().remove()
        self.retry_requested = False
        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://%s/wifi\r\n" % self.local_ip
        )
        return b"", headers

    def consume_retry_request(self):
        if self.retry_requested:
            self.retry_requested = False
            return True
        return False

    def portal_home(self, params):
        status = {}
        if self.portal_status_getter:
            try:
                status = self.portal_status_getter()
            except Exception as e:
                print("portal status getter failed:", e)
        state = status.get("state", "AP_PORTAL_ACTIVE")
        last_error = status.get("last_error", "NONE")
        ip = self.local_ip.decode() if isinstance(self.local_ip, bytes) else self.local_ip
        body = """\
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Growlight Portal</title>
  <style>
    body {{ font-family: sans-serif; background: #f2f4f7; margin: 0; padding: 16px; }}
    .card {{ max-width: 520px; margin: 0 auto; background: #fff; border-radius: 10px; padding: 16px; }}
    .btn {{ display: inline-block; margin: 6px 6px 0 0; padding: 10px 12px; background: #0b76ef; color: #fff; text-decoration: none; border-radius: 6px; }}
    .btn.secondary {{ background: #57606a; }}
    .status {{ font-size: 14px; color: #333; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Device Portal</h2>
    <div class="status">State: <b>{state}</b><br>Last error: <b>{last_error}</b><br>Portal IP: <b>{ip}</b></div>
    <p><a class="btn" href="/wifi">Wi-Fi Setup</a><a class="btn secondary" href="/settings">Settings</a></p>
    <p><a class="btn secondary" href="/retry_wifi">Retry Existing Credentials</a><a class="btn secondary" href="/reset_wifi">Reset Wi-Fi Credentials</a></p>
  </div>
</body>
</html>
""".format(state=state, last_error=last_error, ip=ip)
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
        return body.encode(), headers

    def redirect_root(self, params):
        headers = (
            b"HTTP/1.1 302 Found\r\n"
            b"Location: /\r\n"
        )
        return b"", headers

    def update_lights(self, params):
        _ensure_settings_loaded()
        # params are bytes → decode to str for parsing
        INT_KEYS = {b'morning', b'daylight', b'evening', b'off'}
        TIME_KEYS = {b't_morning_start', b't_day_start', b't_evening_start', b't_lights_off'}
        ALS_INT_KEYS = {
            b'als_sample_interval_ms',
            b'als_filter_window',
            b'als_threshold_on',
            b'als_threshold_dim',
            b'als_dim_level',
            b'als_ramp_rate_ms',
        }
        ALS_BOOL_KEYS = {b'als_enabled'}
        ALS_STR_KEYS = {b'als_control_mode', b'als_fault_action'}
        EC_INT_KEYS = {b'ec_low_alarm_us', b'ec_high_alarm_us'}
        EC_STR_KEYS = {b'plant_category', b'plant_stage'}

        new_settings = {}
        new_ambient = {}
        new_ec = {}
        try:
            for key, value in params.items():
                if value is None or value == b"":
                    continue  # ignore empty fields so defaults stay intact
                if key in INT_KEYS:
                    try:
                        new_settings[key.decode()] = int(value)
                    except Exception:
                        return b"Invalid integer for %s" % key, b"HTTP/1.1 400 Bad Request\r\n"
                elif key in TIME_KEYS:
                    if len(value) == 5 and value[2:3] == b':':
                        new_settings[key.decode()] = value.decode()
                    else:
                        return b"Invalid time for %s" % key, b"HTTP/1.1 400 Bad Request\r\n"
                elif key in ALS_INT_KEYS:
                    try:
                        new_ambient[key.decode()] = int(value)
                    except Exception:
                        return b"Invalid integer for %s" % key, b"HTTP/1.1 400 Bad Request\r\n"
                elif key in ALS_BOOL_KEYS:
                    new_ambient[key.decode()] = 1 if value in (b"1", b"true", b"True", b"on") else 0
                elif key in ALS_STR_KEYS:
                    new_ambient[key.decode()] = value.decode()
                elif key in EC_INT_KEYS:
                    try:
                        new_ec[key.decode()] = int(value)
                    except Exception:
                        return b"Invalid integer for %s" % key, b"HTTP/1.1 400 Bad Request\r\n"
                elif key in EC_STR_KEYS:
                    new_ec[key.decode()] = value.decode()
                elif key.startswith(b"cal_depth_") or key.startswith(b"cal_liters_"):
                    continue

            LightIntensities.update_values(**new_settings)
            # Validate hysteresis thresholds before applying
            ton = new_ambient.get("als_threshold_on", AmbientLightSettings.settings["als_threshold_on"])
            tdim = new_ambient.get("als_threshold_dim", AmbientLightSettings.settings["als_threshold_dim"])
            if ton >= tdim:
                return b"T_ON must be less than T_DIM/OFF", b"HTTP/1.1 400 Bad Request\r\n"
            if "als_control_mode" in new_ambient:
                mode_val = new_ambient["als_control_mode"].upper()
                if mode_val not in ("DIM", "OFF"):
                    return b"Invalid control mode", b"HTTP/1.1 400 Bad Request\r\n"
                new_ambient["als_control_mode"] = mode_val
            if "als_fault_action" in new_ambient:
                fault_val = new_ambient["als_fault_action"].lower()
                if fault_val not in ("normal", "dim"):
                    return b"Invalid fault action", b"HTTP/1.1 400 Bad Request\r\n"
                new_ambient["als_fault_action"] = fault_val
            AmbientLightSettings.update_values(**new_ambient)

            if "ec_low_alarm_us" in new_ec and "ec_high_alarm_us" in new_ec:
                if new_ec["ec_low_alarm_us"] >= new_ec["ec_high_alarm_us"]:
                    return b"EC low must be less than high limit", b"HTTP/1.1 400 Bad Request\r\n"
            ECSettings.update_values(**new_ec)

            if save_all_settings_to_file():
                return b"Settings saved", b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            else:
                return b"Failed to save settings", b"HTTP/1.1 500 Internal Server Error\r\n"
        except Exception as e:
            try:
                import sys
                sys.print_exception(e)
            except Exception:
                pass
            return b"Failed to update settings", b"HTTP/1.1 500 Internal Server Error\r\n"

    def exit_settings(self, params):
        # Invoke caller-provided callback or reset
        if self.exit_callback:
            try:
                self.exit_callback()
                return b"", b"HTTP/1.1 200 OK\r\n"
            except Exception as e:
                print("exit_settings callback failed:", e)
        try:
            import machine
            machine.reset()
        except Exception:
            pass
        return b"", b"HTTP/1.1 200 OK\r\n"

    def update_cal_point(self, params):
        _ensure_settings_loaded()
        _ensure_gpio_loaded()
        try:
            idx_raw = params.get(b"point", None)
            depth_raw = params.get(b"depth", None)
            liters_raw = params.get(b"liters", None)
            if idx_raw is None or liters_raw in (None, b""):
                return b"Missing calibration params", b"HTTP/1.1 400 Bad Request\r\n"
            try:
                idx = int(idx_raw)
            except Exception:
                return b"Invalid point index", b"HTTP/1.1 400 Bad Request\r\n"
            if not 1 <= idx <= 5:
                return b"Point must be 1-5", b"HTTP/1.1 400 Bad Request\r\n"
            depth_ref = WaterCalibration.DEPTH_POINTS[idx - 1]
            try:
                liters_val = float(liters_raw)
            except Exception:
                return b"Invalid liters value", b"HTTP/1.1 400 Bad Request\r\n"

            # Capture the live touch reading as calibration input.
            try:
                raw_depth = read_water_raw(WATER_CAL_SAMPLES, WATER_CAL_DELAY_MS)
            except Exception:
                raw_depth = None

            existing = list(WaterCalibration.points)
            if len(existing) < 5:
                existing = WaterCalibration.default_points[:]
            existing[idx - 1] = {"depth_mm": depth_ref, "liters": round(liters_val, 1), "raw_depth_mm": raw_depth}
            WaterCalibration.update_points(existing)
            if save_all_settings_to_file():
                body = b"Calibration point %d saved" % idx
                headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                return body, headers
            else:
                return b"Failed to save calibration", b"HTTP/1.1 500 Internal Server Error\r\n"
        except Exception as e:
            try:
                import sys
                sys.print_exception(e)
            except Exception:
                pass
            return b"Failed to update calibration", b"HTTP/1.1 500 Internal Server Error\r\n"

    def current_settings(self, params):
        _ensure_settings_loaded()
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            payload = dict(LightIntensities.settings)
            payload["water_calibration"] = WaterCalibration.to_serializable()
            payload["ambient"] = AmbientLightSettings.to_serializable()
            payload["ec"] = ECSettings.to_serializable()
            return ujson.dumps(payload).encode(), headers
        except Exception:
            return ujson.dumps({'error': 'unable to read settings'}).encode(), b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"

    def _get_ec_adc(self):
        _ensure_gpio_loaded()
        if self.ec_adc is None:
            self.ec_adc = setup_adc()
        return self.ec_adc

    def _get_ambient_adc(self):
        _ensure_gpio_loaded()
        if self.ambient_adc is None:
            self.ambient_adc = setup_ambient_adc()
        return self.ambient_adc

    def _read_ec_live(self):
        _ensure_gpio_loaded()
        set_ec_power(True)
        try:
            utime.sleep_ms(EC_SETTLE_MS)
            return read_ec_value(self._get_ec_adc())
        finally:
            set_ec_power(False)

    def _read_water_live(self):
        _ensure_settings_loaded()
        _ensure_gpio_loaded()
        raw = read_water_raw(WATER_LIVE_SAMPLES, WATER_LIVE_DELAY_MS)
        depth = water_raw_to_depth_mm(raw) if raw is not None else None
        liters = depth_to_liters(depth)
        return {"depth_mm": depth, "liters": liters, "raw": raw}

    def _read_ambient_live(self):
        _ensure_settings_loaded()
        _ensure_gpio_loaded()
        status = dict(ambient_status)
        try:
            raw = read_ambient_raw(self._get_ambient_adc())
            status["raw"] = raw
            status["filtered"] = raw
            if not status.get("mode"):
                status["mode"] = "UNKNOWN"
        except Exception as e:
            print("ambient live sample failed:", e)
        return status

    def live_status(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        payload = {}
        try:
            payload["ec"] = {"value": self._read_ec_live()}
        except Exception as e:
            print("EC live sample failed:", e)
            payload["ec"] = {"value": None, "error": "unable to read EC"}

        try:
            payload["water"] = self._read_water_live()
        except Exception as e:
            print("water live sample failed:", e)
            payload["water"] = {"depth_mm": None, "liters": None, "raw": None, "error": "unable to read water"}

        try:
            payload["ambient"] = self._read_ambient_live()
        except Exception as e:
            print("ambient live status failed:", e)
            payload["ambient"] = {"raw": None, "filtered": None, "mode": "UNKNOWN", "fault": False, "error": "unable to read ambient"}

        return ujson.dumps(payload).encode(), headers

    def adc_value(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
        try:
            voltage = self._read_ec_live()
            return str(voltage).encode(), headers
        except Exception:
            return b"error", b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\n"

    def water_level(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            return ujson.dumps(self._read_water_live()).encode(), headers
        except Exception:
            return ujson.dumps({'error': 'unable to read water level'}).encode(), b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"

    def ambient_status(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            return ujson.dumps(self._read_ambient_live()).encode(), headers
        except Exception:
            return ujson.dumps({'error': 'unable to read ambient status'}).encode(), b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"

    def connected(self, params):
        headers = b"HTTP/1.1 200 OK\r\n"
        body = open("./connected.html", "rb").read() % (self.ssid, self.local_ip)
        return body, headers

    def get_response(self, req):
        """generate a response body and headers, given a route"""

        headers = b"HTTP/1.1 200 OK\r\n"
        route = self.routes.get(req.path, None)

        if type(route) is bytes:
            # expect a filename, so return contents of file
            path = route.decode() if isinstance(route, bytes) else route
            return open(route, "rb"), self._file_headers(path)

        if callable(route):
            # call a function, which may or may not return a response
            response = route(req.params)
            body = response[0] or b""
            headers = response[1] or headers
            headers = self._ensure_content_length(headers, body)
            return uio.BytesIO(body), headers

        headers = self._ensure_content_length(b"HTTP/1.1 404 Not Found\r\n", b"")
        return uio.BytesIO(b""), headers

    def is_valid_req(self, req):
        return req.path in self.routes
