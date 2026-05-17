import uerrno
import uio
import uselect as select
import usocket as socket
import ujson

try:
    import micropython
except ImportError:
    class _MicroPythonCompat:
        @staticmethod
        def native(fn):
            return fn
    micropython = _MicroPythonCompat()

from collections import namedtuple
from credentials import Creds
from shared_settings import (
    LightIntensities,
    AmbientLightSettings,
    ECSettings,
    WaterCalibration,
    ambient_status,
    save_all_settings_to_file,
    load_all_settings_from_file,
    depth_to_liters,
)
from gpio import setup_adc, read_ec_value, read_water_depth_mm, read_water_raw, water_raw_to_depth_mm, setup_ambient_adc, read_ambient_raw

WriteConn = namedtuple("WriteConn", ["body", "buff", "buffmv", "write_range"])
ReqInfo = namedtuple("ReqInfo", ["type", "path", "params", "host"])

from server import Server

import gc


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


class HTTPServer(Server):
    def __init__(self, poller, local_ip, mode="wifi", exit_callback=None, portal_status_getter=None):
        super().__init__(poller, 80, socket.SOCK_STREAM, "HTTP Server")
        if type(local_ip) is bytes:
            self.local_ip = local_ip
        else:
            self.local_ip = local_ip.encode()
        self.request = dict()
        self.conns = dict()
        self.exit_callback = exit_callback
        self.portal_status_getter = portal_status_getter
        self.mode = mode
        self.retry_requested = False
        if mode == "settings":
            try:
                load_all_settings_from_file()
            except Exception as e:
                print("HTTP settings server: failed to load settings:", e)
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
                b"/generate_204": self.redirect_root,
                b"/gen_204": self.redirect_root,
                b"/hotspot-detect.html": self.redirect_root,
                b"/ncsi.txt": self.redirect_root,
                b"/redirect": self.redirect_root,
            }

        self.ssid = None

        # queue up to 5 connection requests before refusing
        self.sock.listen(5)
        self.sock.setblocking(False)

    def set_ip(self, new_ip, new_ssid):
        """update settings after connected to local WiFi"""

        self.local_ip = new_ip.encode()
        self.ssid = new_ssid
        self.routes = {b"/": self.connected}

    @micropython.native
    def handle(self, sock, event, others):
        if sock is self.sock:
            # client connecting on port 80, so spawn off a new
            # socket to handle this connection
            print("- Accepting new HTTP connection")
            self.accept(sock)
        elif event & select.POLLIN:
            # socket has data to read in
            print("- Reading incoming HTTP data")
            self.read(sock)
        elif event & select.POLLOUT:
            # existing connection has space to send more data
            print("- Sending outgoing HTTP data")
            self.write_to(sock)

    def accept(self, server_sock):
        """accept a new client request socket and register it for polling"""

        try:
            client_sock, addr = server_sock.accept()
        except OSError as e:
            if e.args[0] == uerrno.EAGAIN:
                return

        client_sock.setblocking(False)
        client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.poller.register(client_sock, select.POLLIN)

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
        Creds(ssid=ssid, password=password).write()
        # Always request an immediate retry after login, even if credentials
        # are unchanged from what's already stored.
        self.retry_requested = True

        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://{:s}/wifi\r\n".format(self.local_ip)
        )

        return b"", headers

    def retry_wifi(self, params):
        self.retry_requested = True
        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://{:s}/\r\n".format(self.local_ip)
        )
        return b"", headers

    def reset_wifi(self, params):
        Creds().remove()
        self.retry_requested = False
        headers = (
            b"HTTP/1.1 307 Temporary Redirect\r\n"
            b"Location: http://{:s}/wifi\r\n".format(self.local_ip)
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
                body = b"""\
<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2;url=/"><title>Saved</title></head><body><h2>Settings saved</h2><p>Device is applying changes. You can close this page.</p></body></html>
"""
                headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                return body, headers
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
            except Exception as e:
                print("exit_settings callback failed:", e)
        try:
            import machine
            machine.reset()
        except Exception:
            pass
        return b"", b"HTTP/1.1 200 OK\r\n"

    def update_cal_point(self, params):
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
                raw_depth = read_water_raw()
            except Exception:
                raw_depth = None

            existing = list(WaterCalibration.points)
            if len(existing) < 5:
                existing = WaterCalibration.default_points[:]
            existing[idx - 1] = {"depth_mm": depth_ref, "liters": round(liters_val, 1), "raw_depth_mm": raw_depth}
            WaterCalibration.update_points(existing)
            if save_all_settings_to_file():
                body = b"""\
<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content=\"1;url=/\"><title>Calibration saved</title></head><body><h3>Calibration point saved</h3><p>Point %d set: depth ref %.1f mm, liters %.1f, raw touch %.1f.</p></body></html>
""" % (idx, depth_ref, liters_val, raw_depth if raw_depth is not None else -1)
                headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
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
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            payload = dict(LightIntensities.settings)
            payload["water_calibration"] = WaterCalibration.to_serializable()
            payload["ambient"] = AmbientLightSettings.to_serializable()
            payload["ec"] = ECSettings.to_serializable()
            return ujson.dumps(payload).encode(), headers
        except Exception:
            return ujson.dumps({'error': 'unable to read settings'}).encode(), b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"

    def adc_value(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
        try:
            adc = setup_adc()
            voltage = read_ec_value(adc)
            return str(voltage).encode(), headers
        except Exception:
            return b"error", b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\n"

    def water_level(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            raw = read_water_raw()
            depth = water_raw_to_depth_mm(raw) if raw is not None else None
            liters = depth_to_liters(depth)
            return ujson.dumps({'depth_mm': depth, 'liters': liters, 'raw': raw}).encode(), headers
        except Exception:
            return ujson.dumps({'error': 'unable to read water level'}).encode(), b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"

    def ambient_status(self, params):
        headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        try:
            status = dict(ambient_status)
            # If no runtime status exists, provide a quick live sample
            if status.get("raw") is None:
                try:
                    adc = setup_ambient_adc()
                    raw = read_ambient_raw(adc)
                    status["raw"] = raw
                    status["filtered"] = raw
                    status["mode"] = "UNKNOWN"
                except Exception as e:
                    print("ambient_status live sample failed:", e)
            return ujson.dumps(status).encode(), headers
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
            return open(route, "rb"), headers

        if callable(route):
            # call a function, which may or may not return a response
            response = route(req.params)
            body = response[0] or b""
            headers = response[1] or headers
            return uio.BytesIO(body), headers

        headers = b"HTTP/1.1 404 Not Found\r\n"
        return uio.BytesIO(b""), headers

    def is_valid_req(self, req):
        host = req.host
        try:
            host = host.split(b":", 1)[0]
        except Exception:
            pass
        if host != self.local_ip:
            # force a redirect to the MCU's IP address
            return False
        # redirect if we don't have a route for the requested path
        return req.path in self.routes

    def read(self, s):
        """read in client request from socket"""

        data = s.read()
        if not data:
            # no data in the TCP stream, so close the socket
            self.close(s)
            return

        # add new data to the full request
        sid = id(s)
        self.request[sid] = self.request.get(sid, b"") + data

        # check if additional data expected
        if data[-4:] != b"\r\n\r\n":
            # HTTP request is not finished if no blank line at the end
            # wait for next read event on this socket instead
            return

        # get the completed request
        req = self.parse_request(self.request.pop(sid))

        if not self.is_valid_req(req):
            headers = (
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                b"Location: http://{:s}/\r\n".format(self.local_ip)
            )
            body = uio.BytesIO(b"")
            self.prepare_write(s, body, headers)
            return

        # by this point, we know the request has the correct
        # host and a valid route
        body, headers = self.get_response(req)
        self.prepare_write(s, body, headers)

    def prepare_write(self, s, body, headers):
        # add newline to headers to signify transition to body
        headers += "\r\n"
        # TCP/IP MSS is 536 bytes, so create buffer of this size and
        # initially populate with header data
        buff = bytearray(headers + "\x00" * (536 - len(headers)))
        # use memoryview to read directly into the buffer without copying
        buffmv = memoryview(buff)
        # start reading body data into the memoryview starting after
        # the headers, and writing at most the remaining space of the buffer
        # return the number of bytes written into the memoryview from the body
        bw = body.readinto(buffmv[len(headers) :], 536 - len(headers))
        # save place for next write event
        c = WriteConn(body, buff, buffmv, [0, len(headers) + bw])
        self.conns[id(s)] = c
        # let the poller know we want to know when it's OK to write
        self.poller.modify(s, select.POLLOUT)

    def write_to(self, sock):
        """write the next message to an open socket"""

        # get the data that needs to be written to this socket
        sid = id(sock)
        c = self.conns.get(sid)
        if c is None:
            self.close(sock)
            return
        if c:
            # write next 536 bytes (max) into the socket
            try:
                bytes_written = sock.write(c.buffmv[c.write_range[0] : c.write_range[1]])
            except OSError:
                print('cannot write to a closed socket')
                self.close(sock)
                return
            if not bytes_written or c.write_range[1] < 536:
                # either we wrote no bytes, or we wrote < TCP MSS of bytes
                # so we're done with this connection
                self.close(sock)
            else:
                # more to write, so read the next portion of the data into
                # the memoryview for the next send event
                self.buff_advance(c, bytes_written)

    def buff_advance(self, c, bytes_written):
        """advance the writer buffer for this connection to next outgoing bytes"""

        if bytes_written == c.write_range[1] - c.write_range[0]:
            # wrote all the bytes we had buffered into the memoryview
            # set next write start on the memoryview to the beginning
            c.write_range[0] = 0
            # set next write end on the memoryview to length of bytes
            # read in from remainder of the body, up to TCP MSS
            c.write_range[1] = c.body.readinto(c.buff, 536)
        else:
            # didn't read in all the bytes that were in the memoryview
            # so just set next write start to where we ended the write
            c.write_range[0] += bytes_written

    def close(self, s):
        """close the socket, unregister from poller, and delete connection"""

        try:
            self.poller.unregister(s)
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass
        sid = id(s)
        if sid in self.request:
            del self.request[sid]
        if sid in self.conns:
            del self.conns[sid]
        gc.collect()
