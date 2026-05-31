import gc
from micropython import const
import network
import ubinascii as binascii
import uselect as select
import utime as time

from captive_dns import DNSServer
from captive_http import HTTPServer
from credentials import Creds
import status_led


class CaptivePortal:
    AP_IP = "192.168.4.1"
    WLAN_INIT_RETRIES = const(8)
    WLAN_RETRY_DELAY_MS = const(800)
    BOOT_GRACE_MS = const(2000)

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
        self.poller = select.poll()

        self.state = self.STATE_BOOT
        self.last_error = None
        self._last_scan_ms = 0
        self._last_scan_ssid_present = False
        self._no_ssid_since_ms = None
        self._backoff_s = self.BACKOFF_MIN
        self._ap_creds_sig = None
        self._ap_last_bg_retry_ms = 0

    def _status_snapshot(self):
        return {
            "state": self.state,
            "last_error": self.last_error or "NONE",
        }

    def _credentials_sig(self):
        self.creds.load(quiet=True)
        return (self.creds.ssid, self.creds.password)

    def _reset_wlan_state(self):
        for iface in (network.STA_IF, network.AP_IF):
            try:
                wlan = network.WLAN(iface)
                wlan.active(False)
            except Exception:
                pass

    def _ensure_interfaces(self):
        if self.sta_if is not None and self.ap_if is not None:
            return True

        self._reset_wlan_state()
        gc.collect()
        for attempt in range(1, self.WLAN_INIT_RETRIES + 1):
            try:
                self.sta_if = network.WLAN(network.STA_IF)
                self.ap_if = network.WLAN(network.AP_IF)
                return True
            except Exception as e:
                print("Failed to init WLAN in CaptivePortal (attempt {}/{}): {}".format(
                    attempt, self.WLAN_INIT_RETRIES, e
                ))
                self.sta_if = None
                self.ap_if = None
                self._reset_wlan_state()
                gc.collect()
                time.sleep_ms(self.WLAN_RETRY_DELAY_MS)
        return False

    def _ssid_exists(self, force=False):
        now = time.ticks_ms()
        if (not force) and time.ticks_diff(now, self._last_scan_ms) < self.SCAN_INTERVAL_NO_SSID * 1000:
            return self._last_scan_ssid_present

        self._last_scan_ms = now
        self._last_scan_ssid_present = False

        if not self.creds.load(quiet=True).is_valid():
            return False
        if not self._ensure_interfaces():
            return False

        try:
            self.sta_if.active(True)
            target = self.creds.ssid
            for row in self.sta_if.scan():
                try:
                    seen = row[0]
                    if isinstance(seen, str):
                        seen = seen.encode("utf-8")
                    if seen == target:
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
            return ip not in ("0.0.0.0", b"0.0.0.0", None)
        except Exception:
            return False

    def _connect_attempt(self, attempt_idx=1, force_scan=False):
        status_led.set_connecting()

        if not self._ensure_interfaces():
            return False, self.FAIL_OTHER

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

        try:
            self.ap_if.active(False)
        except Exception:
            pass

        try:
            self.sta_if.active(True)
            try:
                self.sta_if.disconnect()
            except Exception:
                pass
            self.sta_if.connect(self.creds.ssid, self.creds.password)
        except Exception as e:
            print("Failed to start STA connect:", e)
            return False, self.FAIL_OTHER

        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < self.STA_CONNECT_TIMEOUT * 1000:
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
            status_led.tick()
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
        status_led.set_connecting()
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
            status_led.tick()
            time.sleep_ms(250)

    def _run_backoff_until_connected_or_ap(self):
        self.state = self.STATE_STA_RETRY_BACKOFF
        status_led.set_connecting()
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
        if not self._ensure_interfaces():
            print("Cannot start AP: WLAN init failed")
            return False

        started = False
        for attempt in range(1, 4):
            try:
                self.ap_if.active(False)
                time.sleep_ms(100)
                self.ap_if.active(True)
                time.sleep_ms(250)
                if self.ap_if.active():
                    started = True
                    break
            except Exception as e:
                print("AP start attempt {} failed: {}".format(attempt, e))

        if not started:
            print("Access point failed to start after retries")
            return False

        self._configure_ap_identity()
        ap_pass = self.creds.get_ap_password()
        try:
            self.ap_if.ifconfig((self.AP_IP, "255.255.255.0", self.AP_IP, self.AP_IP))
            if ap_pass and len(ap_pass) >= 8:
                self.ap_if.config(
                    essid=self.essid,
                    authmode=network.AUTH_WPA_WPA2_PSK,
                    password=ap_pass,
                    channel=1,
                    hidden=False,
                    max_clients=4,
                )
            else:
                self.ap_if.config(
                    essid=self.essid,
                    authmode=network.AUTH_OPEN,
                    channel=1,
                    hidden=False,
                    max_clients=4,
                )
            print("AP mode configured:", self.ap_if.ifconfig())
            return True
        except Exception as e:
            print("Failed to configure AP:", e)
            return False

    def _background_sta_retry_due(self):
        now = time.ticks_ms()
        return time.ticks_diff(now, self._ap_last_bg_retry_ms) >= self.BG_RETRY_WHILE_AP * 1000

    def _attempt_sta_from_ap(self):
        self.creds.load(quiet=True)
        if not self.creds.is_valid():
            print("AP retry skipped: no valid credentials")
            return False

        if not self._ssid_exists(force=True):
            self.last_error = self.FAIL_NO_SSID
            print("AP retry failed: {}".format(self.last_error))
            return False

        ok, failure = self._connect_attempt(attempt_idx=self.FAST_RETRY_COUNT, force_scan=True)
        if not ok:
            self.last_error = failure
            print("AP retry failed: {}".format(self.last_error))
            return False
        print("AP retry succeeded")
        return True

    def captive_portal(self, mode="wifi"):
        print("Starting captive portal (AP+DNS+HTTP) mode=", mode)
        self.portal_mode = mode
        self.state = self.STATE_AP_PORTAL_ACTIVE
        if self.creds.load(quiet=True).is_valid():
            status_led.set_connecting()
        else:
            status_led.off()

        if not self.start_access_point():
            return

        if self.http_server is None:
            self.http_server = HTTPServer(
                self.poller,
                self.local_ip,
                mode=mode,
                exit_callback=self.exit_callback,
                portal_status_getter=self._status_snapshot,
            )
            print("Configured HTTP server (portal) mode=", mode)
        if self.dns_server is None:
            self.dns_server = DNSServer(self.poller, self.local_ip)
            print("Configured DNS server (portal)")

        self._ap_creds_sig = self._credentials_sig()
        self._ap_last_bg_retry_ms = time.ticks_ms()

        try:
            while True:
                gc.collect()
                status_led.tick()
                for response in self.poller.ipoll(250):
                    sock, event, *others = response
                    is_handled = self.handle_dns(sock, event, others)
                    if not is_handled:
                        self.handle_http(sock, event, others)

                manual_retry = self.http_server.consume_retry_request()
                if manual_retry:
                    print("Manual/POST login retry requested")
                    self._last_scan_ms = 0

                if mode == "settings":
                    if manual_retry:
                        if self._attempt_sta_from_ap():
                            print("Connected to WiFi from settings mode")
                            if self.exit_callback:
                                self.exit_callback()
                                return
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

    def handle_dns(self, sock, event, others):
        if sock is self.dns_server.sock:
            if event == select.POLLHUP:
                return True
            self.dns_server.handle(sock, event, others)
            return True
        return False

    def handle_http(self, sock, event, others):
        self.http_server.handle(sock, event, others)

    def cleanup(self, keep_sta=False):
        print("Cleaning up")
        try:
            if self.dns_server:
                try:
                    self.dns_server.stop(self.poller)
                except Exception:
                    pass
            if self.ap_if:
                try:
                    self.ap_if.active(False)
                except Exception:
                    pass
            if self.http_server:
                try:
                    self.http_server.stop(self.poller)
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

    def start(self, force_ap=False):
        self.state = self.STATE_BOOT
        status_led.off()
        time.sleep_ms(self.BOOT_GRACE_MS)

        for attempt in range(1, 4):
            if self._ensure_interfaces():
                break
            print("WLAN init retry at boot ({}/3)".format(attempt))
            time.sleep_ms(500)
        else:
            print("Cannot start captive portal: WLAN init failed at boot")
            return

        self.creds.load()
        if force_ap:
            self.last_error = None
            status_led.off()
            return self.captive_portal(mode="wifi")

        if not self.creds.is_valid():
            self.last_error = self.FAIL_NO_SSID
            status_led.off()
            return self.captive_portal(mode="wifi")

        ok, failure = self._run_fast_retries()
        if ok:
            return

        self.last_error = failure
        if failure == self.FAIL_AUTH:
            return self.captive_portal(mode="wifi")

        self._backoff_s = self.BACKOFF_MIN
        self._no_ssid_since_ms = time.ticks_ms() if failure == self.FAIL_NO_SSID else None
        ok, backoff_failure = self._run_backoff_until_connected_or_ap()
        if ok:
            return

        self.last_error = backoff_failure
        self.captive_portal(mode="wifi")
