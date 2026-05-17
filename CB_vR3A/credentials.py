import ujson
import uos

try:
    import ubinascii
    import machine
except Exception:
    ubinascii = None
    machine = None


class Creds:
    CRED_FILE = "./wifi.creds"
    CONFIG_FILE = "./config.json"
    DEBUG_AP_PASS = "grow1234"

    def __init__(self, ssid=None, password=None):
        self.ssid = ssid
        self.password = password

    def write(self):
        """Persist Wi-Fi credentials to config.json and legacy wifi.creds."""
        if self.is_valid():
            cfg = self._load_config()
            wifi = cfg.get("wifi", {})
            wifi["ssid"] = self._to_text(self.ssid)
            wifi["password"] = self._to_text(self.password)
            wifi["last_success_epoch"] = wifi.get("last_success_epoch", 0) or 0
            cfg["wifi"] = wifi

            device = cfg.get("device", {})
            device["ap_pass"] = device.get("ap_pass", self._default_ap_pass())
            cfg["device"] = device

            self._save_config(cfg)
            print("Wrote credentials to {:s}".format(self.CONFIG_FILE))
            try:
                with open(self.CRED_FILE, "wb") as f:
                    f.write(self.ssid + b"," + self.password)
                print("Wrote credentials to {:s}".format(self.CRED_FILE))
            except Exception as e:
                print("Failed writing {:s}: {}".format(self.CRED_FILE, e))

    def load(self, quiet=False):
        # Preferred source: config.json
        try:
            cfg = self._load_config()
            wifi = cfg.get("wifi", {})
            ssid = wifi.get("ssid", "")
            password = wifi.get("password", "")
            if ssid:
                self.ssid = self._to_bytes(ssid)
                self.password = self._to_bytes(password)
                if not quiet:
                    print("Loaded WiFi credentials from {:s}".format(self.CONFIG_FILE))
                return self
        except OSError:
            pass

        # Backward-compatible fallback: wifi.creds
        try:
            with open(self.CRED_FILE, "rb") as f:
                contents = f.read().split(b",")
            if len(contents) == 2:
                self.ssid, self.password = contents
                if self.is_valid():
                    self.write()  # migrate to config.json
                    if not quiet:
                        print("Migrated WiFi credentials from {:s}".format(self.CRED_FILE))
        except OSError:
            pass

        return self

    def remove(self):
        """
        Clear stored Wi-Fi credentials while preserving other config.
        """
        cfg = self._load_config()
        wifi = cfg.get("wifi", {})
        wifi["ssid"] = ""
        wifi["password"] = ""
        wifi["last_success_epoch"] = 0
        cfg["wifi"] = wifi
        self._save_config(cfg)

        try:
            uos.remove(self.CRED_FILE)
        except OSError:
            pass

        self.ssid = self.password = None

    def update_last_success(self, epoch):
        cfg = self._load_config()
        wifi = cfg.get("wifi", {})
        wifi["last_success_epoch"] = int(epoch) if epoch is not None else 0
        cfg["wifi"] = wifi
        self._save_config(cfg)

    def get_ap_password(self):
        cfg = self._load_config()
        device = cfg.get("device", {})
        ap_pass = self.DEBUG_AP_PASS
        if device.get("ap_pass") != ap_pass:
            device["ap_pass"] = ap_pass
            cfg["device"] = device
            self._save_config(cfg)
        return ap_pass

    def is_valid(self):
        # SSID must exist; password may be empty for open networks.
        if not isinstance(self.ssid, bytes):
            return False
        if not isinstance(self.password, bytes):
            return False
        return bool(self.ssid)

    def _load_config(self):
        try:
            with open(self.CONFIG_FILE, "r") as f:
                cfg = ujson.load(f)
                if isinstance(cfg, dict):
                    return cfg
        except Exception:
            pass
        return {}

    def _save_config(self, cfg):
        with open(self.CONFIG_FILE, "w") as f:
            ujson.dump(cfg, f)

    def _default_ap_pass(self):
        try:
            if machine and ubinascii:
                uid = ubinascii.hexlify(machine.unique_id()).decode()
                return "grow-" + uid[-8:]
        except Exception:
            pass
        return "growlight123"

    def _to_text(self, val):
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return str(val)

    def _to_bytes(self, val):
        if isinstance(val, bytes):
            return val
        return str(val).encode("utf-8")
