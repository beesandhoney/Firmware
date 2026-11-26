import uerrno
import uio
import usocket as socket
import ujson
import uos
import utime

from collections import namedtuple

ReqInfo = namedtuple("ReqInfo", ["type", "path", "params", "host"])

import gc

REQUEST_READ_TIMEOUT_S = 1.0
RESPONSE_CHUNK_SIZE = 536
HTTP_LISTEN_BACKLOG = 6
RESPONSE_WRITE_RETRIES = 300
RESPONSE_RETRY_DELAY_MS = 10
RESPONSE_CLOSE_GRACE_MS = 50
EC_SETTLE_MS = 200
MAX_REQUEST_BODY = 2048

LightIntensities = None
AmbientLightSettings = None
ECSettings = None
WaterCalibration = None
ambient_status = None
save_all_settings_to_file = None
load_all_settings_from_file = None
_settings_loaded = False
Creds = None

setup_adc = None
read_ec_value = None
set_ec_power = None
setup_ambient_adc = None
read_ambient_raw = None


def _ensure_settings_loaded():
    global LightIntensities, AmbientLightSettings, ECSettings, WaterCalibration
    global ambient_status, save_all_settings_to_file, load_all_settings_from_file
    global _settings_loaded

    if LightIntensities is None:
        from shared_settings import (
            LightIntensities as LightIntensities_mod,
            AmbientLightSettings as AmbientLightSettings_mod,
            ECSettings as ECSettings_mod,
            WaterCalibration as WaterCalibration_mod,
            ambient_status as ambient_status_mod,
            save_all_settings_to_file as save_all_settings_to_file_mod,
            load_all_settings_from_file as load_all_settings_from_file_mod,
        )
        LightIntensities = LightIntensities_mod
        AmbientLightSettings = AmbientLightSettings_mod
        ECSettings = ECSettings_mod
        WaterCalibration = WaterCalibration_mod
        ambient_status = ambient_status_mod
        save_all_settings_to_file = save_all_settings_to_file_mod
        load_all_settings_from_file = load_all_settings_from_file_mod

    if not _settings_loaded:
        try:
            load_all_settings_from_file()
        except Exception as e:
            print("HTTP settings load failed:", e)
        _settings_loaded = True


def _ensure_gpio_loaded():
    global setup_adc, read_ec_value, set_ec_power
    global setup_ambient_adc, read_ambient_raw

    if setup_adc is None:
        from firmware_loader import load
        load("gpio")
        from gpio import (
            setup_adc as setup_adc_mod,
            read_ec_value as read_ec_value_mod,
            set_ec_power as set_ec_power_mod,
            setup_ambient_adc as setup_ambient_adc_mod,
            read_ambient_raw as read_ambient_raw_mod,
        )
        setup_adc = setup_adc_mod
        read_ec_value = read_ec_value_mod
        set_ec_power = set_ec_power_mod
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
    def __init__(self, poller, local_ip, mode="wifi", exit_callback=None,
                 portal_status_getter=None, listen_sock=None, networks_getter=None,
                 memory_reporter=None):
        self.name = "HTTP Server"
        self.sock = listen_sock
        gc.collect()
        stage = "socket"
        try:
            if self.sock is None:
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
                # serve_once supplies any requested idle delay explicitly.
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
        self.networks_getter = networks_getter
        self.memory_reporter = memory_reporter
        self.main_available = True
        self._exit_pending = False
        self._exit_sent_ms = None
        self.retry_requested = False
        self.ec_adc = None
        self.ec_continuous_enabled = False
        self.ambient_adc = None
        self.routes = {
            b"/": b"./settings.html",
            b"/garden": b"./settings.html",
            b"/wifi.js": b"./wifi.js",
            b"/api/portal": self.api_portal,
            b"/api/networks": self.api_networks,
            b"/api/wifi": self.api_wifi,
            b"/api/portal/exit": self.exit_settings,
            b"/settings": b"./settings.html",
            b"/grow-settings": b"./settings.html",
            b"/grow.css": b"./grow.css",
            b"/grow.js": b"./grow.js",
            b"/api/settings": self.api_settings,
            b"/api/status": self.api_status,
            b"/wifi": b"./index.html",
            b"/login": self.login,
            b"/retry_wifi": self.retry_wifi,
            b"/reset_wifi": self.reset_wifi,
            b"/update_settings": self.update_lights,
            b"/update_lights": self.update_lights,
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
        for icon in ('moon', 'shade', 'sunrise', 'brightness', 'plant-test-tube', 'weather-temperature', 'sunset'):
            path = ('icons/' + icon + '-svgrepo-com.svg').encode()
            self.routes[b'/' + path] = b'./' + path

        self.ssid = None
        print('HTTP build: portal-routing-3')

    def serve_once(self, idle_ms=250):
        client_sock = None
        try:
            client_sock, addr = self.sock.accept()
        except OSError as e:
            # Only idle/transient errors are harmless. Let the portal rebuild a
            # broken listening socket instead of silently failing forever.
            if not self.is_transient_socket_error(e):
                raise
            if idle_ms:
                utime.sleep_ms(idle_ms)
            return True

        try:
            self._response_path = b'<unparsed>'
            response_sent = self.handle_blocking(client_sock)
            if response_sent:
                # Let lwIP queue the final TCP segment before close().
                utime.sleep_ms(RESPONSE_CLOSE_GRACE_MS)
            return True
        except Exception as e:
            if not self.is_transient_socket_error(e):
                print("HTTP blocking handler failed:", e)
            return False
        finally:
            try:
                client_sock.close()
            except Exception as e:
                # A queued response does not prove the connection was released.
                # Retain visibility of failed close operations under low memory.
                print('HTTP close failed: path={} errno={}'.format(
                    self._response_path, e.args[0] if e.args else None))
            gc.collect()

    def handle_blocking(self, client_sock):
        self._response_path = b'<unparsed>'
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

        # Read the small form body even when TCP splits it after the headers.
        try:
            head, received = data.split(b"\r\n\r\n", 1)
            length = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            if not 0 <= length <= MAX_REQUEST_BODY:
                raise ValueError('Request body too large')
            while len(received) < length:
                chunk = client_sock.recv(min(512, length - len(received)))
                if not chunk:
                    raise ValueError('Incomplete request body')
                received += chunk
            data = head + b"\r\n\r\n" + received[:length]
        except (ValueError, OSError):
            return self.write_blocking(client_sock, uio.BytesIO(b'Invalid request body'),
                                      self._ensure_content_length(b'HTTP/1.1 400 Bad Request\r\n', b'Invalid request body'))

        try:
            req = self.parse_request(data)
        except Exception as e:
            print("HTTP bad request:", e)
            return
        # Only the explicit captive-network probe routes redirect to the portal.
        # Missing browser icons/assets must not trigger another full HTML transfer.
        body, headers = self.get_response(req)

        # Log only the route, never query strings or submitted credentials.
        self._response_path = req.path
        response_sent = self.write_blocking(client_sock, body, headers)
        if response_sent and req.path in (b'/api/portal/exit', b'/exit_settings') and req.type == b'POST' and self._exit_sent_ms is None:
            self._exit_sent_ms = utime.ticks_ms()
        if not response_sent:
            print("HTTP response delivery failed for", req.path)
        return response_sent

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
        extension = path.rsplit('.', 1)[-1]
        content_type = {'svg': b'image/svg+xml', 'css': b'text/css', 'js': b'application/javascript'}.get(extension, b'text/html')
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: " + content_type + b"; charset=utf-8\r\n"
            b"Cache-Control: no-store\r\n"
            b"Connection: close\r\n"
        )
        try:
            headers += b"Content-Length: %d\r\n" % uos.stat(path)[6]
        except Exception:
            pass
        return headers

    def _log_send_event(self, event, code):
        print("HTTP TX: path={} event={} errno={} sent={} elapsed_ms={}".format(
            getattr(self, '_response_path', b'<unknown>'), event, code,
            self._response_bytes,
            utime.ticks_diff(utime.ticks_ms(), self._response_started_ms)))
        # Snapshot both heaps at the first stall, before changing memory with GC.
        if event in ('backpressure', 'no-progress'):
            reporter = getattr(self, 'memory_reporter', None)
            if reporter:
                try:
                    reporter('HTTP first send stall')
                except Exception:
                    pass  # Diagnostics must not interrupt socket cleanup.

    def _write_all_blocking(self, client_sock, data):
        if not data:
            return True
        view = memoryview(data)
        offset = 0
        total = len(data)
        retries = 0
        while offset < total:
            try:
                # send() reports partial progress. Stream write() may time out after
                # sending part of the buffer without reporting that byte count.
                written = client_sock.send(view[offset:])
            except OSError as e:
                code = e.args[0] if e.args else None
                # A reset/aborted/broken connection cannot recover by sleeping.
                # Keep the existing retry budget only for temporary send stalls.
                if code in (uerrno.EAGAIN, getattr(uerrno, 'EWOULDBLOCK', 11),
                            getattr(uerrno, 'ETIMEDOUT', 116), 116):
                    if not self._response_stalled:
                        self._response_stalled = True
                        self._log_send_event('backpressure', code)
                    retries += 1
                    if retries >= RESPONSE_WRITE_RETRIES:
                        self._log_send_event('retry-limit', code)
                        return False
                    utime.sleep_ms(RESPONSE_RETRY_DELAY_MS)
                    continue
                self._log_send_event('socket-error', code)
                return False
            if not written:
                if not self._response_stalled:
                    self._response_stalled = True
                    self._log_send_event('no-progress', written)
                retries += 1
                if retries >= RESPONSE_WRITE_RETRIES:
                    self._log_send_event('retry-limit', written)
                    return False
                utime.sleep_ms(RESPONSE_RETRY_DELAY_MS)
                continue
            offset += written
            self._response_bytes += written
            retries = 0
        return True

    def write_blocking(self, client_sock, body, headers):
        self._response_started_ms = utime.ticks_ms()
        self._response_bytes = 0
        self._response_stalled = False
        print('HTTP TX: start path={}'.format(getattr(self, '_response_path', b'<unknown>')))
        try:
            # Bound each send and retry partial writes while lwIP drains its queue.
            # The short request-read timeout must not become the response deadline.
            try:
                client_sock.setblocking(False)
            except AttributeError:
                pass
            headers = self._ensure_connection_close(headers)
            if not self._write_all_blocking(client_sock, headers + b"\r\n"):
                return False
            while True:
                chunk = body.read(RESPONSE_CHUNK_SIZE)
                if not chunk:
                    break
                if not self._write_all_blocking(client_sock, chunk):
                    return False
            self._log_send_event('complete', None)
            return True
        finally:
            try:
                body.close()
            except Exception:
                pass

    def set_ip(self, new_ip, new_ssid):
        """update settings after connected to local WiFi"""

        self.local_ip = new_ip.encode()
        self.ssid = new_ssid
        self.main_available = True
        self.routes[b"/"] = b"./settings.html"

    def stop(self, poller=None):
        if self.ec_continuous_enabled and set_ec_power is not None:
            try:
                set_ec_power(False)
            except Exception as e:
                print("Failed to switch off continuous EC power:", e)
            self.ec_continuous_enabled = False
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        print(self.name, "stopped")

    def parse_request(self, req):
        """parse a raw HTTP request to get items of interest"""

        head, _, body = req.partition(b"\r\n\r\n")
        req_lines = head.split(b"\r\n")
        req_type, full_path, http_ver = req_lines[0].split(b" ")
        path = full_path.split(b"?")
        base_path = path[0]
        query = path[1] if len(path) > 1 else None
        query_params = {}
        if req_type == b'POST':
            if len(body) > MAX_REQUEST_BODY:
                raise ValueError('Request body too large')
            if not any(line.lower().startswith(b'content-type: application/x-www-form-urlencoded') for line in req_lines):
                raise ValueError('Unsupported request content type')
            query = body
        if query:
            for param in query.split(b"&"):
                if b"=" in param:
                    key, val = param.split(b"=", 1)
                else:
                    key, val = param, b""
                query_params[unquote(key.replace(b"+", b" "))] = unquote(val.replace(b"+", b" "))
        hosts = [line.split(b": ")[1] for line in req_lines if b"Host:" in line]
        host = hosts[0] if hosts else self.local_ip

        return ReqInfo(req_type, base_path, query_params, host)

    def api_settings(self, params, method):
        _ensure_settings_loaded()
        from firmware_loader import load
        ui = load('grow_ui')
        status = b'200 OK'
        try:
            if method == b'GET':
                payload = ui.settings()
            elif method == b'POST':
                payload = ui.save_settings(params)
            else:
                status, payload = b'405 Method Not Allowed', {'error': 'Use GET or POST.'}
        except ValueError as error:
            status, payload = b'400 Bad Request', {'error': str(error)}
        except OSError as error:
            status, payload = b'500 Internal Server Error', {'error': str(error)}
        return ujson.dumps(payload).encode(), b'HTTP/1.1 ' + status + b'\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n'

    def api_status(self, params, method):
        if method != b'GET':
            return b'{"error":"Use GET."}', b'HTTP/1.1 405 Method Not Allowed\r\nContent-Type: application/json\r\n'
        _ensure_settings_loaded()
        from firmware_loader import load
        ui = load('grow_ui')
        payload = ui.status(self._read_ec_live, self._read_water_live)
        return ujson.dumps(payload).encode(), b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n'

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
        from local_time import TIMEZONES, valid_schedule_time
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
                elif key == b'timezone':
                    zone = value.decode()
                    if zone not in TIMEZONES:
                        return b"Invalid time zone", b"HTTP/1.1 400 Bad Request\r\n"
                    new_settings['timezone'] = zone
                elif key in TIME_KEYS:
                    if valid_schedule_time(value.decode()):
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

    def _json(self, payload, status=b'200 OK'):
        return ujson.dumps(payload).encode(), b'HTTP/1.1 ' + status + b'\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n'

    def api_portal(self, params, method):
        if method != b'GET':
            return self._json({'error': 'Use GET.'}, b'405 Method Not Allowed')
        payload = self.portal_status_getter() if self.portal_status_getter else {}
        payload.update(main_available=self.main_available, exit_available=True)
        return self._json(payload)

    def api_networks(self, params, method):
        if method != b'GET':
            return self._json({'error': 'Use GET.'}, b'405 Method Not Allowed')
        try:
            if self.networks_getter is None:
                raise OSError('Network scanner unavailable')
            networks = self.networks_getter()
            if not isinstance(networks, list):
                raise ValueError('Invalid scan result')
            return self._json({'networks': networks})
        except Exception as error:
            print('Wi-Fi scan failed:', error)
            return self._json({'error': 'Network scan failed. Retry or enter the network name.'}, b'503 Service Unavailable')

    def api_wifi(self, params, method):
        if method != b'POST':
            return self._json({'error': 'Use POST.'}, b'405 Method Not Allowed')
        ssid, password = params.get(b'ssid', b''), params.get(b'password', b'')
        if not ssid or len(ssid) > 32 or (password and not 8 <= len(password) <= 63):
            return self._json({'error': 'Enter a network name and an 8–63 character password, or leave it empty for an open network.'}, b'400 Bad Request')
        try:
            _get_creds_class()(ssid=ssid, password=password).write()
        except Exception:
            return self._json({'error': 'Could not save Wi-Fi credentials. Please retry.'}, b'500 Internal Server Error')
        self.retry_requested = True
        return self._json({'message': 'Connecting to Wi-Fi…'}, b'202 Accepted')

    def exit_settings(self, params, method):
        if method != b'POST':
            return self._json({'error': 'Use POST after confirming exit.'}, b'405 Method Not Allowed')
        self._exit_pending = True
        return self._json({'message': 'Portal closed', 'closing': True})

    def exit_due(self):
        # Start the grace period only after the response has reached the socket.
        return self._exit_sent_ms is not None and utime.ticks_diff(utime.ticks_ms(), self._exit_sent_ms) >= 2000

    def current_settings(self, params):
        _ensure_settings_loaded()
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            payload = dict(LightIntensities.settings)
            from local_time import status as local_time_status
            payload['clock'] = local_time_status(payload['timezone'])
            payload["water_sensor_type"] = WaterCalibration.SENSOR_TYPE
            import hardware_config
            payload["water_metering_enabled"] = hardware_config.installed()
            payload["capabilities"] = dict(water_level=payload["water_metering_enabled"])
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
        if self.mode == "settings":
            if not self.ec_continuous_enabled:
                set_ec_power(True)
                self.ec_continuous_enabled = True
                utime.sleep_ms(EC_SETTLE_MS)
            return read_ec_value(self._get_ec_adc())

        set_ec_power(True)
        try:
            utime.sleep_ms(EC_SETTLE_MS)
            return read_ec_value(self._get_ec_adc())
        finally:
            set_ec_power(False)

    def _read_water_live(self):
        _ensure_settings_loaded()
        from volume_http import read_live
        return read_live()

    def _read_ambient_live(self):
        _ensure_settings_loaded()
        _ensure_gpio_loaded()
        status = dict(ambient_status)
        from local_time import status as local_time_status
        status['clock'] = local_time_status(LightIntensities.settings['timezone'])
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
            payload["water"] = {"volume_l": None, "raw": None, "error": "unable to read water"}

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
        body = open("./connected.html", "rb").read()
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
            try:
                response = route(req.params, req.type) if req.path.startswith(b'/api/') or req.path == b'/exit_settings' else route(req.params)
            except Exception as e:
                print("HTTP route failed:", req.path, e)
                if isinstance(e, ImportError):
                    message = "Firmware module unavailable: {}. Upload all generated _fw_*.mpy files, then restart.".format(e)
                else:
                    message = "Device request failed: {}. Check the serial log.".format(e)
                body = message.encode()
                headers = b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n"
                return uio.BytesIO(body), self._ensure_content_length(headers, body)
            body = response[0] or b""
            headers = response[1] or headers
            headers = self._ensure_content_length(headers, body)
            return uio.BytesIO(body), headers

        headers = self._ensure_content_length(b"HTTP/1.1 404 Not Found\r\n", b"")
        return uio.BytesIO(b""), headers

    def is_valid_req(self, req):
        return req.path in self.routes
