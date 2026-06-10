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

    def __init__(self, essid=None):
        self.local_ip = self.AP_IP
        self.sta_if = None
        self.ap_if = None
        self.portal_mode = "wifi"
        self.exit_callback = None

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
        self._ap_started_ms = 0
        self._last_ap_station_check_ms = 0
        self._last_ap_station_count = None
        self._station_status_supported = None

    def _status_snapshot(self):
        return {
            "state": self.state,
            "last_error": self.last_error or "NONE",
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

        # Soft resets can leave ESP-IDF netifs/driver state behind even when
        # this CaptivePortal instance has no cached WLAN objects yet.
        for iface in (network.STA_IF, network.AP_IF):
            try:
                wlan = network.WLAN(iface)
                try:
                    wlan.disconnect()
                except Exception:
                    pass
                try:
                    wlan.active(False)
                except Exception:
                    pass
            except Exception as e:
                print("Global WLAN reset skipped iface {}: {}".format(iface, e))
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
        try:
            from captive_http import HTTPServer

            self.http_server = HTTPServer(
                None,
                self.local_ip,
                mode=mode,
                exit_callback=self.exit_callback,
                portal_status_getter=self._status_snapshot,
            )
            print("Configured HTTP server mode=", mode)
            self._poll_error_count = 0
            return True
        except Exception as e:
            print("Failed to start HTTP server:", e)
            self._print_mem_free("mem_free after HTTP start failure:")
            self.http_server = None
            return False

    def _poll_once(self, timeout_ms=250, mode=None):
        if self.http_server is None:
            return False
        try:
            self.http_server.serve_once(timeout_ms)
            self._poll_error_count = 0
            return True
        except OSError as e:
            self._poll_error_count += 1
            print("HTTP poll failed ({}): {}".format(self._poll_error_count, e))
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

        ssid_present = self._ssid_exists(force=force_scan)
        if not ssid_present:
            if force_scan:
                # Some APs/hotspots may not appear reliably in scan results.
                # Still attempt a direct connect before classifying as no-SSID.
                print("SSID not seen in scan; attempting direct connect anyway")
            else:
                return False, self.FAIL_NO_SSID

        if not keep_ap:
            try:
                if self.ap_if:
                    self.ap_if.active(False)
            except Exception:
                pass

        try:
            self.sta_if.active(True)
            try:
                self.sta_if.disconnect()
            except Exception:
                pass
            self.sta_if.connect(
                self._as_text(self.creds.ssid),
                self._as_text(self.creds.password),
            )
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
                self.ap_if.ifconfig((self.AP_IP, "255.255.255.0", self.AP_IP, self.AP_IP))
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

    def _attempt_sta_from_ap(self):
        self.creds.load(quiet=True)
        if not self.creds.is_valid():
            print("AP retry skipped: no valid credentials")
            return False

        if not self._ssid_exists(force=True):
            self.last_error = self.FAIL_NO_SSID
            print("AP retry failed: {}".format(self.last_error))
            return False

        ok, failure = self._connect_attempt(
            attempt_idx=self.FAST_RETRY_COUNT,
            force_scan=True,
            keep_ap=True,
        )
        if not ok:
            self.last_error = failure
            print("AP retry failed: {}".format(self.last_error))
            self.start_access_point()
            return False
        print("AP retry succeeded")
        return True

    def _start_portal_network(self, mode):
        for attempt in range(1, self.PORTAL_HTTP_START_ATTEMPTS + 1):
            if attempt > 1:
                print("Retrying portal AP+HTTP start ({}/{})".format(
                    attempt, self.PORTAL_HTTP_START_ATTEMPTS
                ))
                self.cleanup(keep_sta=False)
                self._hard_reset_wlan("portal HTTP retry")

            if not self.start_access_point():
                return False

            self._ap_started_ms = time.ticks_ms()
            self._last_ap_station_check_ms = 0
            self._last_ap_station_count = None
            self._station_status_supported = None

            print("AP ready; starting HTTP server")
            if self._start_http_server(mode):
                return True

            print("HTTP server unavailable after AP start")

        self.cleanup(keep_sta=False)
        return False

    def captive_portal(self, mode="wifi"):
        print("Starting captive portal (AP+DNS+HTTP) mode=", mode)
        self.portal_mode = mode
        self.state = self.STATE_AP_PORTAL_ACTIVE
        status_led.off()

        self._stop_http_server()
        self._hard_reset_wlan("portal start")

        if not self._start_portal_network(mode):
            print("Portal start failed; HTTP server never became available")
            return False

        self.dns_server = None
        print("DNS server disabled; open http://{}/ manually".format(self.AP_IP))

        self._ap_creds_sig = self._credentials_sig()
        self._ap_last_bg_retry_ms = time.ticks_ms()
        last_loop_service_ms = time.ticks_ms()

        try:
            while True:
                now = time.ticks_ms()
                if time.ticks_diff(now, last_loop_service_ms) >= 1000:
                    last_loop_service_ms = now
                    self._service()
                    self._ap_station_count()
                if self.http_server is None:
                    time.sleep_ms(250)
                else:
                    self._poll_once(250, mode=mode)

                manual_retry = self.http_server.consume_retry_request() if self.http_server else False
                if manual_retry:
                    print("Manual/POST login retry requested")
                    self._last_scan_ms = 0

                if mode == "settings":
                    if manual_retry:
                        if self._attempt_sta_from_ap():
                            print("Connected to WiFi from settings mode")
                            if self.exit_callback:
                                self.exit_callback()
                                return True
                            break
                    continue

                # Save&Connect flow: changed credentials trigger immediate retry.
                creds_sig = self._credentials_sig()
                creds_changed = creds_sig != self._ap_creds_sig
                if creds_changed:
                    self._ap_creds_sig = creds_sig
                    self._last_scan_ms = 0

                if creds_changed or manual_retry or self._background_sta_retry_due():
                    self._ap_last_bg_retry_ms = time.ticks_ms()
                    if self._attempt_sta_from_ap():
                        print("Connected to WiFi from AP portal")
                        break

        except KeyboardInterrupt:
            print("Captive portal stopped")
            self.cleanup(keep_sta=True)
            return False

        self.cleanup(keep_sta=True)
        return self._is_sta_connected_with_ip()

    def poll_http(self, timeout_ms=0):
        if self.http_server is None:
            return
        self._poll_once(timeout_ms, mode="sta")

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
        status_led.off()
        self._wait_seconds(max(1, self.BOOT_GRACE_MS // 1000))

        self.creds.load()
        if force_ap:
            self.last_error = None
            return self.captive_portal(mode="wifi_portal")

        if not self.creds.is_valid():
            self.last_error = self.FAIL_NO_SSID
            status_led.off()
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
