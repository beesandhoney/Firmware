import gc
from micropython import const
import network
import ubinascii as binascii
import utime as time

from credentials import Creds
import status_led


class CaptivePortal:
    AP_IP = "192.168.4.1"
    SETTINGS_AP_ESSID = "GrowSettings"
    WIFI_SETUP_AP_ESSID = "GrowWiFiSetup"
    WLAN_INIT_RETRIES = const(8)
    WLAN_RETRY_DELAY_MS = const(800)
    BOOT_GRACE_MS = const(2000)
    WLAN_HARD_RESET_DELAY_MS = const(1200)

    # State names
    STATE_BOOT = "BOOT"
    STATE_TRY_STA = "TRY_STA"
    STATE_STA_CONNECTED = "STA_CONNECTED"
    STATE_STA_RETRY_BACKOFF = "STA_RETRY_BACKOFF"
    STATE_AP_PORTAL_ACTIVE = "AP_PORTAL_ACTIVE"

    # Fail classifications
    FAIL_AUTH = "FAIL_AUTH"
    FAIL_NO_SSID = "FAIL_NO_SSID"
    FAIL_TIMEOUT = "FAIL_TIMEOUT"
    FAIL_OTHER = "FAIL_OTHER"

    # Timing constants (seconds)
    STA_CONNECT_TIMEOUT = const(20)
    FAST_RETRY_COUNT = const(3)
    FAST_RETRY_DELAYS = (5, 10, 30)
    BACKOFF_MIN = const(60)
    BACKOFF_MAX = const(30 * 60)
    AP_AUTO_START_AFTER_NO_SSID = const(120)
    BG_RETRY_WHILE_AP = const(10 * 60)
    SCAN_INTERVAL_NO_SSID = const(10)
    AP_HEALTH_CHECK_MS = const(5000)
    AP_CONFIG_RETRIES = const(3)
    AP_STATION_CHECK_MS = const(1000)
    PORTAL_HTTP_START_ATTEMPTS = const(2)
    HTTP_RESTART_INTERVAL_MS = const(2000)

    def __init__(self, essid=None):
        self.local_ip = self.AP_IP
        self.sta_if = None
        self.ap_if = None
        self.portal_mode = "wifi"
        self.exit_callback = None
        self.portal_closed = False
        self.exit_requested = False
        self.connection_pending = False

        if essid is None:
            essid = b"Growlight"
        self.essid_prefix = essid
        self.essid = essid
        self._essid_finalized = False

        self.creds = Creds()
        self.dns_server = None
        self.http_server = None
        self.service_callback = None

        self.state = self.STATE_BOOT
        self.last_error = None
        self._last_scan_ms = 0
        self._last_scan_ssid_present = False
        self._no_ssid_since_ms = None
        self._backoff_s = self.BACKOFF_MIN
        self._ap_creds_sig = None
        self._ap_last_bg_retry_ms = 0
        self._last_ap_health_ms = 0
        self._poll_error_count = 0
        self._last_http_restart_ms = None
        self._ap_started_ms = 0
        self._last_ap_station_check_ms = 0
        self._last_ap_station_count = None
        self._station_status_supported = None

    def _status_snapshot(self):
        connected = self._is_sta_connected_with_ip()
        return {
            "state": self.state,
            "last_error": self.last_error or "NONE",
            "connected": connected,
            "connecting": self.connection_pending or bool(self.http_server and self.http_server.retry_requested),
            "ssid": self._as_text(self.creds.ssid) if self.creds.ssid else "",
            "ip": self.sta_if.ifconfig()[0] if connected else None,
        }

    def _service(self):
        status_led.tick()
        if self.service_callback:
            try:
                self.service_callback()
            except Exception as e:
                print("CaptivePortal service callback failed:", e)

    def _credentials_sig(self):
        self.creds.load(quiet=True)
        return (self.creds.ssid, self.creds.password)

    def _as_bytes(self, value):
        if isinstance(value, bytes):
            return value
        if value is None:
            return b""
        return str(value).encode("utf-8")

    def _as_text(self, value):
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if value is None:
            return ""
        return str(value)

    def _reset_wlan_state(self):
        for wlan in (self.sta_if, self.ap_if):
            if wlan is None:
                continue
            try:
                try:
                    wlan.disconnect()
                except Exception:
                    pass
                wlan.active(False)
            except Exception:
                pass

        # Do not construct fresh WLAN objects merely to deactivate them. On a
        # clean boot that initializes the Wi-Fi driver during "cleanup", and a
        # failed initialization can leave duplicate ESP-NETIF keys behind.
        self.sta_if = None
        self.ap_if = None

    def _sleep_service_ms(self, delay_ms):
        end = time.ticks_add(time.ticks_ms(), delay_ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self._service()
            time.sleep_ms(100)

    def _hard_reset_wlan(self, reason=""):
        if reason:
            print("Hard resetting WLAN:", reason)
        else:
            print("Hard resetting WLAN")
        self._reset_wlan_state()
        gc.collect()
        self._sleep_service_ms(self.WLAN_HARD_RESET_DELAY_MS)
        gc.collect()

    def _print_mem_free(self, label):
        try:
            print(label, gc.mem_free())
        except Exception:
            pass

    def _stop_http_server(self):
        if self.http_server:
            try:
                self.http_server.stop()
            except Exception:
                pass
            self.http_server = None

    def _start_http_server(self, mode):
        self._stop_http_server()
        gc.collect()
        self._print_mem_free("mem_free before HTTP start:")
        self._print_network_heap()
        listen_sock = None
        try:
            # Reserve the lwIP socket while the larger pre-import heap is still
            # available. Importing captive_http afterwards previously left too
            # little contiguous networking memory and could abort the ESP32.
            import usocket as portal_socket
            listen_sock = portal_socket.socket()
            try:
                listen_sock.setsockopt(portal_socket.SOL_SOCKET, portal_socket.SO_REUSEADDR, 1)
            except Exception as e:
                print("Portal socket SO_REUSEADDR skipped:", e)
            listen_sock.bind(("0.0.0.0", 80))
            listen_sock.listen(6)
            # Runtime polling must never wait for an incoming connection.
            listen_sock.setblocking(False)
            print("HTTP listening socket reserved before module load")

            from captive_http import HTTPServer
            # Module import creates temporary parser/import objects. Free those
            # before asking lwIP for its listening socket.
            gc.collect()
            self._print_mem_free("mem_free after HTTP load:")
            self._print_network_heap()

            self.http_server = HTTPServer(
                None,
                self.local_ip,
                mode=mode,
                exit_callback=self.exit_callback,
                portal_status_getter=self._status_snapshot,
                listen_sock=listen_sock,
                networks_getter=self.scan_networks,
                memory_reporter=self._print_network_heap,
            )
            listen_sock = None  # HTTPServer now owns the socket.
            print("Configured HTTP server mode=", mode)
            self._poll_error_count = 0
            return True
        except Exception as e:
            if listen_sock is not None:
                try:
                    listen_sock.close()
                except Exception:
                    pass
            print("Failed to start HTTP server:", e)
            self._print_mem_free("mem_free after HTTP start failure:")
            self.http_server = None
            return False

    def _poll_once(self, timeout_ms=250, mode=None):
        if self.http_server is None:
            now = time.ticks_ms()
            if (self._last_http_restart_ms is not None and
                    time.ticks_diff(now, self._last_http_restart_ms) < self.HTTP_RESTART_INTERVAL_MS):
                return False
            self._last_http_restart_ms = now
            print("Recovering HTTP listener")
            if not self._start_http_server(mode or self.portal_mode):
                return False
        try:
            self.http_server.serve_once(timeout_ms)
            self._poll_error_count = 0
            return True
        except OSError as e:
            self._poll_error_count += 1
            print("HTTP listener failed ({}): {}".format(self._poll_error_count, e))
            self._stop_http_server()
            return False

    def _ap_station_count(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_ap_station_check_ms) < self.AP_STATION_CHECK_MS:
            return self._last_ap_station_count

        self._last_ap_station_check_ms = now
        if not self.ap_if:
            self._last_ap_station_count = None
            return None

        try:
            stations = self.ap_if.status("stations")
            self._station_status_supported = True
        except Exception as e:
            if self._station_status_supported is not False:
                print("AP station status unavailable:", e)
            self._station_status_supported = False
            self._last_ap_station_count = None
            return None

        try:
            count = len(stations)
        except Exception:
            count = 0

        prev_count = self._last_ap_station_count
        if count != prev_count:
            print("AP station count:", count)
            self._print_network_heap()
        self._last_ap_station_count = count
        return count

    def _recover_wlan_after_sta_start_failure(self, preserve_ap=False):
        print("Recovering WLAN after STA start failure")
        try:
            if self.sta_if:
                try:
                    self.sta_if.disconnect()
                except Exception:
                    pass
                try:
                    self.sta_if.active(False)
                except Exception:
                    pass
            if self.ap_if:
                if preserve_ap:
                    try:
                        self.ap_if.active(True)
                    except Exception:
                        pass
                else:
                    try:
                        self.ap_if.active(False)
                    except Exception:
                        pass
        except Exception:
            pass
        self.sta_if = None
        if not preserve_ap:
            self.ap_if = None
            self._hard_reset_wlan("STA start failure")
            return
        gc.collect()
        self._sleep_service_ms(500)

    def _ensure_sta(self):
        if self.sta_if is not None:
            return True

        gc.collect()
        for attempt in range(1, self.WLAN_INIT_RETRIES + 1):
            self._service()
            try:
                self.sta_if = network.WLAN(network.STA_IF)
                return True
            except Exception as e:
                print("Failed to init STA WLAN (attempt {}/{}): {}".format(
                    attempt, self.WLAN_INIT_RETRIES, e
                ))
                self._print_mem_free("mem_free after STA WLAN init failure:")
                self.sta_if = None
                if "out of memory" in str(e).lower():
                    print("STA WLAN memory failure requires a device reset")
                    return False
                gc.collect()
                time.sleep_ms(self.WLAN_RETRY_DELAY_MS)
        return False

    def _ensure_ap(self):
        if self.ap_if is not None:
            return True

        gc.collect()
        for attempt in range(1, self.WLAN_INIT_RETRIES + 1):
            self._service()
            try:
                self.ap_if = network.WLAN(network.AP_IF)
                return True
            except Exception as e:
                print("Failed to init AP WLAN (attempt {}/{}): {}".format(
                    attempt, self.WLAN_INIT_RETRIES, e
                ))
                self._print_mem_free("mem_free after AP WLAN init failure:")
                self.ap_if = None
                if "out of memory" in str(e).lower():
                    print("AP WLAN memory failure requires a device reset")
                    return False
                gc.collect()
                time.sleep_ms(self.WLAN_RETRY_DELAY_MS)
        return False

    def _ensure_interfaces(self):
        return self._ensure_sta() and self._ensure_ap()

    def _ssid_exists(self, force=False):
        now = time.ticks_ms()
        if (not force) and time.ticks_diff(now, self._last_scan_ms) < self.SCAN_INTERVAL_NO_SSID * 1000:
            return self._last_scan_ssid_present

        self._last_scan_ms = now
        self._last_scan_ssid_present = False

        if not self.creds.load(quiet=True).is_valid():
            return False
        if not self._ensure_sta():
            return False

        try:
            self.sta_if.active(True)
            target = self._as_bytes(self.creds.ssid)
            for row in self.sta_if.scan():
                try:
                    seen = row[0]
                    if self._as_bytes(seen) == target:
                        self._last_scan_ssid_present = True
                        break
                except Exception:
                    pass
        except Exception as e:
            print("SSID scan failed:", e)

        return self._last_scan_ssid_present

    def _is_sta_connected_with_ip(self):
        try:
            if not self.sta_if or not self.sta_if.isconnected():
                return False
            ip = self.sta_if.ifconfig()[0]
            ip = self._as_text(ip)
            return bool(ip) and ip != "0.0.0.0"
        except Exception:
            return False

    def _connect_attempt(self, attempt_idx=1, force_scan=False, keep_ap=False):
        if not self._ensure_sta():
            return False, self.FAIL_OTHER
        status_led.set_connecting()

        self.creds.load(quiet=True)
        if not self.creds.is_valid():
            return False, self.FAIL_NO_SSID

        # A synchronous ESP32 scan is memory-heavy and may appear to hang.
        # Try the saved SSID directly first; scan only on later attempts so it
        # can help classify an actual connection failure.
        ssid_present = None
        if force_scan and attempt_idx > 1:
            print("Scanning for saved SSID before STA attempt", attempt_idx)
            ssid_present = self._ssid_exists(force=True)
            if not ssid_present:
                print("SSID not seen in scan; attempting direct connect anyway")
        else:
            print("Starting direct STA connection attempt", attempt_idx)

        if not keep_ap:
            try:
                if self.ap_if:
                    self.ap_if.active(False)
            except Exception:
                pass

        try:
            print("Activating STA interface")
            self.sta_if.active(True)
            try:
                self.sta_if.disconnect()
            except Exception:
                pass
            print("Connecting to saved SSID")
            self.sta_if.connect(
                self._as_text(self.creds.ssid),
                self._as_text(self.creds.password),
            )
            print("STA connection started")
        except Exception as e:
            print("Failed to start STA connect:", e)
            self._recover_wlan_after_sta_start_failure(preserve_ap=keep_ap)
            return False, self.FAIL_OTHER

        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < self.STA_CONNECT_TIMEOUT * 1000:
            self._service()
            if self._is_sta_connected_with_ip():
                ip = self.sta_if.ifconfig()[0]
                self.local_ip = ip.decode() if isinstance(ip, bytes) else ip
                self.state = self.STATE_STA_CONNECTED
                self.last_error = None
                try:
                    self.creds.update_last_success(time.time())
                except Exception:
                    pass
                print("Connected with IP:", self.local_ip)
                status_led.set_connected()
                return True, None
            time.sleep_ms(250)

        try:
            status = self.sta_if.status()
            if status == network.STAT_WRONG_PASSWORD:
                print("STA status indicates wrong password")
                return False, self.FAIL_AUTH
        except Exception:
            pass

        if ssid_present is False:
            return False, self.FAIL_NO_SSID

        if attempt_idx >= self.FAST_RETRY_COUNT:
            return False, self.FAIL_TIMEOUT
        return False, self.FAIL_TIMEOUT

    def _run_fast_retries(self):
        self.state = self.STATE_TRY_STA
        last_failure = self.FAIL_OTHER
        for attempt in range(1, self.FAST_RETRY_COUNT + 1):
            ok, failure = self._connect_attempt(attempt_idx=attempt, force_scan=True)
            if ok:
                return True, None
            last_failure = failure
            self.last_error = failure
            status_led.set_connection_failed()
            print("STA fast attempt {} failed with {}".format(attempt, failure))
            if failure == self.FAIL_AUTH:
                print("Stopping fast retries due to authentication failure")
                return False, self.FAIL_AUTH
            if attempt < self.FAST_RETRY_COUNT:
                delay = self.FAST_RETRY_DELAYS[min(attempt - 1, len(self.FAST_RETRY_DELAYS) - 1)]
                self._wait_seconds(delay)
        return False, last_failure

    def _wait_seconds(self, seconds):
        end = time.ticks_add(time.ticks_ms(), seconds * 1000)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self._service()
            time.sleep_ms(250)

    def _run_backoff_until_connected_or_ap(self):
        self.state = self.STATE_STA_RETRY_BACKOFF
        while True:
            delay_s = self._backoff_s
            print("STA backoff waiting {}s".format(delay_s))
            self._wait_seconds(delay_s)

            ok, failure = self._connect_attempt(attempt_idx=1, force_scan=True)
            if ok:
                return True, None

            self.last_error = failure
            status_led.set_connection_failed()
            now = time.ticks_ms()

            if failure == self.FAIL_NO_SSID:
                if self._no_ssid_since_ms is None:
                    self._no_ssid_since_ms = now
                missing_ms = time.ticks_diff(now, self._no_ssid_since_ms)
                if missing_ms >= self.AP_AUTO_START_AFTER_NO_SSID * 1000:
                    print("SSID missing for {}s, starting AP portal".format(self.AP_AUTO_START_AFTER_NO_SSID))
                    return False, self.FAIL_NO_SSID
            else:
                self._no_ssid_since_ms = None

            next_backoff = int(self._backoff_s * 2)
            if next_backoff > self.BACKOFF_MAX:
                next_backoff = self.BACKOFF_MAX
            self._backoff_s = next_backoff

    def _configure_ap_identity(self):
        if self.portal_mode == "settings":
            self.essid = self.SETTINGS_AP_ESSID
            self._essid_finalized = True
            return
        if self.portal_mode in ("wifi", "wifi_portal"):
            self.essid = self.WIFI_SETUP_AP_ESSID
            self._essid_finalized = True
            return
        if self._essid_finalized:
            return
        try:
            mac = self.ap_if.config("mac")
            suffix = binascii.hexlify(mac[-2:]).decode()
            if isinstance(self.essid_prefix, bytes):
                self.essid = (self.essid_prefix + b"-" + suffix.encode())
            else:
                self.essid = (str(self.essid_prefix) + "-" + suffix).encode()
            self._essid_finalized = True
        except Exception as e:
            print("AP identity fallback:", e)
            self.essid = b"Growlight-setup"

    def _configure_ap_ipv4(self):
        # Preserve ESP-IDF's DHCP server started by active(True) when the
        # default subnet already matches. On ESP32, writing ifconfig stops
        # and restarts DHCP even if the supplied IPv4 values are unchanged.
        expected = (self.AP_IP, "255.255.255.0", self.AP_IP)
        current = self.ap_if.ifconfig()
        if tuple(current[:3]) != expected:
            self.ap_if.ifconfig(expected + (self.AP_IP,))
            print("AP IPv4 changed; DHCP restart requested")
        else:
            print("AP IPv4 already correct; preserving default DHCP startup")
        current = self.ap_if.ifconfig()
        if tuple(current[:3]) != expected:
            raise RuntimeError("AP IPv4 configuration did not apply")
        self.local_ip = self.AP_IP

    def _print_network_heap(self, context="portal"):
        try:
            from firmware_loader import load
            load('memory_probe').report(context)
        except Exception as e:
            print("Network heap diagnostics unavailable:", e)

    def start_access_point(self):
        for config_attempt in range(1, self.AP_CONFIG_RETRIES + 1):
            if config_attempt > 1:
                self._hard_reset_wlan("AP config retry {}".format(config_attempt))

            if not self._ensure_ap():
                print("Cannot start AP: AP WLAN init failed")
                continue

            try:
                if self.sta_if and not self._is_sta_connected_with_ip():
                    try:
                        self.sta_if.disconnect()
                    except Exception:
                        pass
                    self.sta_if.active(False)
                    self._sleep_service_ms(200)
            except Exception:
                pass

            started = False
            for attempt in range(1, 4):
                self._service()
                try:
                    self.ap_if.active(False)
                    self._sleep_service_ms(200)
                    self.ap_if.active(True)
                    self._sleep_service_ms(500)
                    if self.ap_if.active():
                        started = True
                        break
                except Exception as e:
                    print("AP start attempt {} failed: {}".format(attempt, e))

            if not started:
                print("Access point failed to start after retries")
                continue

            self._configure_ap_identity()
            try:
                essid = self._as_text(self.essid)
                # Setup portals must be easy to join. Use an open AP because
                # captive DNS is optional on this build and users may need to
                # manually browse to http://192.168.4.1/.
                open_setup_ap = self.portal_mode in ("settings", "wifi", "wifi_portal")
                ap_pass = None if open_setup_ap else self.creds.get_ap_password()
                if open_setup_ap:
                    # Keep this path as close as possible to the older
                    # GrowSettings AP setup that was known to associate.
                    self.ap_if.config(
                        essid=essid.encode("utf-8"),
                        authmode=network.AUTH_OPEN,
                        channel=1,
                        max_clients=1,
                    )
                elif ap_pass and len(ap_pass) >= 8:
                    self.ap_if.config(
                        essid=essid,
                        authmode=network.AUTH_WPA_WPA2_PSK,
                        password=ap_pass,
                        channel=1,
                        hidden=False,
                        max_clients=4,
                    )
                else:
                    self.ap_if.config(
                        essid=essid,
                        authmode=network.AUTH_OPEN,
                        channel=1,
                        hidden=False,
                        max_clients=4,
                    )
                self._configure_ap_ipv4()
                self._sleep_service_ms(1000)
                print("AP mode configured:", self.ap_if.ifconfig())
                print("AP SSID:", essid)
                if open_setup_ap:
                    print("AP security: open")
                else:
                    print("AP password:", ap_pass)
                print("Portal URL: http://{}/".format(self.AP_IP))
                try:
                    print("AP active:", self.ap_if.active())
                    print("AP configured SSID:", self.ap_if.config("essid"))
                    print("AP configured channel:", self.ap_if.config("channel"))
                    print("AP configured authmode:", self.ap_if.config("authmode"))
                except Exception:
                    pass
                return True
            except Exception as e:
                print("Failed to configure AP (attempt {}/{}): {}".format(
                    config_attempt, self.AP_CONFIG_RETRIES, e
                ))
                if "out of memory" in str(e).lower():
                    print("AP configuration memory failure requires a device reset")
                    return False

        print("Access point failed to configure after retries")
        return False

    def _ensure_ap_running(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_ap_health_ms) < self.AP_HEALTH_CHECK_MS:
            return
        self._last_ap_health_ms = now

        try:
            if self.ap_if and self.ap_if.active():
                return
        except Exception:
            pass

        print("AP health check: AP inactive, restarting")
        self.start_access_point()

    def _background_sta_retry_due(self):
        return False

    def scan_networks(self):
        if self.connection_pending:
            raise OSError('Connection attempt in progress; retry scan shortly')
        if not self._ensure_sta():
            raise OSError('STA unavailable')
        self.sta_if.active(True)
        names = set()
        for row in self.sta_if.scan():
            try:
                name = row[0].decode('utf-8') if isinstance(row[0], bytes) else row[0]
                if name:
                    names.add(name)
            except UnicodeError:
                pass
        return sorted(names)

    def request_exit(self):
        self.exit_requested = True

    def close_portal(self):
        self.connection_pending = False
        self.cleanup(keep_sta=True)
        self.portal_closed = True
        status_led.set_config_portal(False)
        status_led.set_wifi_connected(self._is_sta_connected_with_ip())

    def _begin_portal_connection(self):
        self.creds.load(quiet=True)
        self.last_error = None
        try:
            if not self.creds.is_valid() or not self._ensure_sta():
                raise ValueError('Invalid credentials')
            self.sta_if.active(True)
            self.sta_if.disconnect()
            self.sta_if.connect(self.creds.ssid, self.creds.password)
            self.connection_pending = True
            self._connection_started_ms = time.ticks_ms()
        except Exception:
            self.last_error = self.FAIL_OTHER
            self.connection_pending = False

    def _service_portal_connection(self):
        if not self.connection_pending:
            return
        if self._is_sta_connected_with_ip():
            self.connection_pending = False
            self.state = self.STATE_STA_CONNECTED
            self.last_error = None
            try:
                self.creds.update_last_success(time.time())
            except Exception:
                pass
            status_led.set_wifi_connected(True)
            if self.http_server:
                self.http_server.main_available = True
                self.http_server.routes[b'/'] = b'./settings.html'
        elif time.ticks_diff(time.ticks_ms(), self._connection_started_ms) >= self.STA_CONNECT_TIMEOUT * 1000:
            self.connection_pending = False
            self.last_error = self.FAIL_TIMEOUT
            try:
                if self.sta_if.status() == network.STAT_WRONG_PASSWORD:
                    self.last_error = self.FAIL_AUTH
            except Exception:
                pass
            # Stop ESP-IDF's background association retries before another scan.
            # Leaving a timed-out attempt running can make scan() return no SSIDs.
            try:
                self.sta_if.disconnect()
            except Exception:
                pass

    def _start_portal_network(self, mode):
        if not self.start_access_point():
            return False

        self._ap_started_ms = time.ticks_ms()
        self._last_ap_station_check_ms = 0
        self._last_ap_station_count = None
        self._station_status_supported = None

        print("AP ready; starting HTTP server")
        for attempt in range(1, self.PORTAL_HTTP_START_ATTEMPTS + 1):
            if self._start_http_server(mode):
                return True

            print("HTTP server unavailable after AP start ({}/{})".format(
                attempt, self.PORTAL_HTTP_START_ATTEMPTS
            ))
            if attempt < self.PORTAL_HTTP_START_ATTEMPTS:
                # A failed socket allocation does not require recreating the
                # Wi-Fi driver. Reinitializing an already active AP can itself
                # fail with "WiFi Out of Memory" on the ESP32.
                gc.collect()
                self._sleep_service_ms(500)

        self.cleanup(keep_sta=False)
        return False

    def captive_portal(self, mode="wifi"):
        print("Portal build: dhcp-preserve-1")
        print("Starting captive portal (AP+DNS+HTTP) mode=", mode)
        self.portal_mode = mode
        self.portal_closed = self.exit_requested = False
        self.state = self.STATE_AP_PORTAL_ACTIVE
        status_led.set_wifi_connected(False)
        status_led.set_config_portal(True)

        self._stop_http_server()
        self._hard_reset_wlan("portal start")

        if not self._start_portal_network(mode):
            print("Portal start failed; HTTP server never became available")
            status_led.set_system_error()
            return False

        # The portal is now a valid operating mode, so operation indication
        # can start even though the full sensor runtime is not loaded.
        status_led.set_operation_started()

        self.dns_server = None
        print("DNS disabled for pre-local-time comparison; open http://192.168.4.1/")

        self._ap_creds_sig = self._credentials_sig()
        self._ap_last_bg_retry_ms = time.ticks_ms()
        try:
            while True:
                self._service()
                if self.dns_server is not None:
                    self.dns_server.serve_once()
                now = time.ticks_ms()
                if time.ticks_diff(now, self._last_ap_station_check_ms) >= self.AP_STATION_CHECK_MS:
                    self._ap_station_count()
                self._poll_once(50, mode=mode)
                if self.http_server is None:
                    time.sleep_ms(50)

                if self.exit_requested or (self.http_server and self.http_server.exit_due()):
                    if self.exit_callback:
                        self.exit_callback()
                    self.close_portal()
                    return self._is_sta_connected_with_ip()
                if self.http_server and self.http_server.consume_retry_request():
                    self._begin_portal_connection()
                self._service_portal_connection()

        except KeyboardInterrupt:
            print("Captive portal stopped")
            self.cleanup(keep_sta=True)
            return False

        self.cleanup(keep_sta=True)
        status_led.set_config_portal(False)
        status_led.set_wifi_connected(self._is_sta_connected_with_ip())
        return self._is_sta_connected_with_ip()

    def poll_http(self, timeout_ms=0):
        if self.portal_closed:
            return
        if self.http_server and self.http_server.exit_due():
            if self.exit_callback:
                self.exit_callback()
            self.close_portal()
            return
        self._poll_once(timeout_ms, mode="sta")
        if self.http_server and self.http_server.consume_retry_request():
            self._begin_portal_connection()
        self._service_portal_connection()

    def start_sta_server(self):
        if not self._is_sta_connected_with_ip():
            print("STA HTTP server not started: STA is not connected")
            return False

        try:
            ip = self.sta_if.ifconfig()[0]
            self.local_ip = self._as_text(ip)
        except Exception:
            pass

        if self.http_server is not None:
            return True

        try:
            print("mem_free before STA HTTP start:", gc.mem_free())
        except Exception:
            pass

        try:
            if not self._start_http_server("sta"):
                return False
            print("STA HTTP server listening at http://{}/".format(self.local_ip))
            try:
                print("mem_free after STA HTTP start:", gc.mem_free())
            except Exception:
                pass
            return True
        except Exception as e:
            print("Failed to start STA HTTP server:", e)
            self.http_server = None
            return False

    def cleanup(self, keep_sta=False):
        print("Cleaning up")
        try:
            print("mem_free before portal cleanup:", gc.mem_free())
        except Exception:
            pass
        try:
            if self.dns_server:
                try:
                    self.dns_server.stop(None)
                except Exception:
                    pass
            if self.ap_if:
                try:
                    self.ap_if.active(False)
                except Exception:
                    pass
            if self.http_server:
                try:
                    self.http_server.stop()
                except Exception:
                    pass

            if not keep_sta and self.sta_if:
                try:
                    self.sta_if.disconnect()
                except Exception:
                    pass
                try:
                    self.sta_if.active(False)
                except Exception:
                    pass
        finally:
            self.ap_if = None
            self.dns_server = None
            self.http_server = None
            gc.collect()
            try:
                print("mem_free after portal cleanup:", gc.mem_free())
            except Exception:
                pass

    def start(self, force_ap=False):
        self.state = self.STATE_BOOT
        self._wait_seconds(max(1, self.BOOT_GRACE_MS // 1000))

        self.creds.load()
        if force_ap:
            self.last_error = None
            return self.captive_portal(mode="wifi_portal")

        if not self.creds.is_valid():
            self.last_error = self.FAIL_NO_SSID
            return self.captive_portal(mode="wifi")

        ok, failure = self._run_fast_retries()
        if ok:
            return True

        self.last_error = failure
        if failure in (self.FAIL_AUTH, self.FAIL_OTHER):
            return self.captive_portal(mode="wifi")

        self._backoff_s = self.BACKOFF_MIN
        self._no_ssid_since_ms = time.ticks_ms() if failure == self.FAIL_NO_SSID else None
        ok, backoff_failure = self._run_backoff_until_connected_or_ap()
        if ok:
            return True

        self.last_error = backoff_failure
        return self.captive_portal(mode="wifi")
