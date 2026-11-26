# Blog post series

The code in this repo is the results of a blog series I wrote about building a captive web portal for a Wemos D1 Mini
using MicroPython. You can find the articles here:

- [Part 1](https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-1/)
- [Part 2](https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-2/)
- [Part 3](https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-3/)
- [Part 4](https://ansonvandoren.com/posts/esp8266-captive-web-portal-part-4/)

# Starting the captive portal

Copy the .py and .html files to your ESP8266 board. If you already have a `main.py` file, then just copy the contents of
the `main.py` file from this repo. There's only a couple of lines there.

Instantiating a `CaptivePortal` and calling its `start()` method will turn on your MCU's WiFi access point, and you can
then connect to it and input your home WiFi credentials. Once you do, the MCU will turn off its AP, and connect to your
home WiFi instead.

# Purpose

This is not really a standalone project, but rather a bit of useful functionality that I drop into other projects
I make so that if I send one to someone else, I don't need to hardcode their home WiFi credentials to get the thing
to work. Instead, they can easily enter their own WiFi SSID and password to allow the device to connect and
start doing whatever it's supposed to be doing.


The current ESP32 build implements the Grow consumer interface described below.
Use [UPLOAD.md](UPLOAD.md) for the complete compiled firmware and web asset set;
the original ESP8266 instructions above are historical.

Light schedules use local time in the saved `timezone` setting, defaulting to
`Europe/Stockholm`. `Europe/Lisbon` and `Europe/London` are also supported.
NTP sets the RTC in UTC. A valid RTC continues driving the local schedule while
internet access is unavailable. An uninitialized clock leaves scheduled lighting
off and shows a time warning; it does not guess the local time after power loss.
Saved times are applied at minute precision, including across midnight.

The Settings page displays the device's local RTC time, or an unavailable message
if the RTC date is uninitialized. The page does not set the RTC or initiate NTP.
Current EU/UK DST rules are implemented in `local_time.py`: transitions at 01:00
UTC on the last Sundays of March and October. These are current scheduling rules,
not a historical IANA database; a future legal rule change requires a firmware
update. At spring-forward, skipped boundaries take effect at the next comparison;
at fall-back, the phase follows the repeated local time. Saved schedule times are
interpreted as local times, without shifting their displayed values.

### Grow web interface — SW-SPEC-UI-001 revision 1.0

`/` and `/garden` display My Garden; `/settings` opens the Settings menu, and
`/grow-settings` opens the controller settings form. All HTML, CSS, JavaScript
and the seven supplied SVG icons are local files. Static files use buffered
transfers. The page refreshes status every five seconds while visible, clears
unconfirmed measurements on connection loss, and retries automatically.

- **Lighting:** actual output ON/OFF, next scheduled transition, time warning,
  ambient adjustment and manual mode. Manual mode lasts until the mode button
  returns to scheduled operation or the device restarts, as defined by
  `MANUAL_OVERRIDE_ENDS` in `app_main.py`.
- **Water:** calibrated volume percentage and litres; below 20% is Low water.
  Invalid, stale or missing calibration/measurements are unavailable to the
  consumer page. The eight-segment indicator remains volume based, 0–100%.
- **Nutrients:** mS/cm, using the same saved low/high limits as firmware alarms.
  Values at either threshold are Optimal. Invalid measurements are unavailable.
- **Temperature:** NTCG163JF103FT1H to ground, 10 kOhm pull-up to 3.3 V on GPIO32.
  `temperature.py` takes the median of five calibrated ADC voltage readings and
  uses R25=10 kOhm and B25/50=3380 K from the
  [TDK data sheet](https://product.tdk.com/system/files/dam/doc/product/sensor/ntc/chip-ntc-thermistor/data_sheet/datasheet_ntcg163jf103ft1h.pdf).
  This beta conversion is an approximation for room temperatures. Readings outside
  the classic ESP32 ADC's recommended 150–2450 mV linear interval are unavailable;
  see the [MicroPython ADC documentation](https://docs.micropython.org/en/latest/esp32/quickref.html#adc-analog-to-digital-conversion).
  Verify installed temperature accuracy against a reference thermometer.

Settings include time zone, ON/OFF times, ambient sensing and nutrient limits.
Times must differ; schedules can cross midnight. Nutrient limits require
0 ≤ low < high ≤ 2.72 mS/cm with at least 0.001 mS/cm separation. All values are
validated before persistence, then a verified temporary file replaces settings.json.
Failure preserves previous active settings and the saved file. No change is saved
until Save settings is pressed. Factory calibration remains REPL-only.

Saving an ON/OFF schedule uses the existing daylight brightness for the entire
ON interval and zero for the OFF interval. Existing morning/day/evening settings
are honored until a new consumer schedule is saved. Ambient sensing retains its
existing algorithm and can reduce scheduled brightness, but cannot turn on the
lights during scheduled night. Disabling it uses scheduled/manual brightness
without ambient adjustment.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Consumer status in %, litres, mS/cm and °C |
| `GET /api/settings` | Saved consumer settings and supported time zones |
| `POST /api/settings` | Complete URL-encoded settings form; validate and save |

The POST fields are `timezone`, `lights_on`, `lights_off`, `ambient_enabled`
(`1`/`0`), `ec_alarm_low` and `ec_alarm_high` (mS/cm; legacy `ec_low_ms`/`ec_high_ms` aliases remain supported). Invalid input returns HTTP 400; persistence
failure returns HTTP 500. Success returns `Settings saved successfully.` and the
saved settings. No factory geometry or calibration is accepted by these APIs.
The normal LAN server and captive portal share this implementation. The optional
Microdot server also serves these endpoints and assets; Wi-Fi provisioning remains
in CaptivePortal.

### Ultrasonic water level — SW-SPEC-WL-001 revision 1.1

TRIG uses GPIO27 and ECHO uses GPIO33. The consumer page displays litres, status
and volume-based fill percentage; the REPL also displays water height. The eight-segment LED bar represents
0–100% of the maximum recommended operating volume.

Factory setup is available only through the MicroPython REPL. Stop the running
application with Ctrl-C, then run:

```python
import factory_calibration
factory_calibration.start()
```

1. Choose **1 New calibration**. Empty the reservoir completely and enter its
   internal bottom diameter in mm. Press Enter to capture the empty distance.
2. Fill to the maximum recommended operating level. Enter the internal diameter
   at that water level, then press Enter to capture the full distance.
3. Review the empty/full distances, calculated effective height and maximum
   operating volume. Keep the result with `y`, redo point `1` or `2`, or cancel
   with `n`. No container height or reference water heights are entered.
4. Optionally choose **3 Verify measurement** at an intermediate water level.
   Enter a known water height to check accuracy within **10 mm**, or press Enter
   to display distance, water height, litres, fill percentage and status only.
   A failed attempted check must be repeated successfully or calibration redone
   before saving. This check is recommended, but is not required for saving a
   new calibration that passed the reference and geometry checks.
5. Choose **4 Save**, review the values, and confirm with `y`. Choose **5 Exit**,
   then restart with Ctrl-D. **2 Show parameters** displays stored/pending data.

Each reference uses the median of 30 samples, requires at least 80% valid
samples, and rejects a peak-to-peak spread above **10 mm**. If a point fails,
press Enter or `r` to remeasure with the same diameter, `e` to edit its diameter,
or `c` to cancel. The other accepted point is retained during retries. An invalid
pair or geometry also allows redoing either point. Nothing is written until the
operator explicitly saves; failed or cancelled replacements retain the prior file.

Effective height is `distance_empty_mm - distance_full_mm`. During operation,
water height is `distance_empty_mm - measured_distance_mm`. The diameters define
a frustum with straight sides; volume is calculated from its partial height,
and fill is the fraction of maximum recommended **volume**, not height. The
method does not correct ultrasonic scale errors or account for displaced volume
from internal components. Out-of-range heights are flagged before display
limiting; fill is always limited to 0–100%.

`factory_water.json` uses the specification's **version 2** schema, with a
`water_level` object containing bottom/full diameters, empty/full distances,
effective height, maximum volume in litres and a calibrated flag. Stored height
and volume are checked against the reference data at startup. Writes use a
validated temporary file followed by rename. Old version-1 and four-point data
require a new empty/full calibration; they are not silently converted or deleted.
Missing, invalid or uncalibrated data produces a configuration fault and no valid
calibrated water level. Invalid echoes retain a visibly stale reading for up to
10 seconds, then clear the display and LED bar.

Configurable engineering defaults in `water_config.py` require effective height
**greater than 50 mm and at most 3980 mm**, **0.1–10000 L** for maximum operating volume, and **20–4000 mm**
for each reference distance. The full distance must be smaller than the empty
distance, and diameters must be finite and positive. Tolerances and sample
settings are in `factory_calibration.py`. Product testing must confirm these
limits and the sensor's installed accuracy. See UPLOAD.md for the version-2
firmware upload set; updating only the factory script is insufficient.

After uploading the firmware set, restart with Ctrl-D, stop the application
with Ctrl-C, then enter these commands to inspect ultrasonic fluctuations
at the `>>>` prompt (do not type the word `python`):

```python
import factory_calibration
factory_calibration.debug()
```

By default this prints a labelled block about once per second: unfiltered
distance and calibrated water height in mm, the
change since the previous valid distance, and median/min/max/peak-to-peak spread
over the last 30 attempts. Invalid echoes are counted and printed explicitly.
Height is unclamped so fluctuations beyond empty/full remain visible. Without
calibration, distance and its statistics still work; height displays `--`.
Press Ctrl-C to stop. Option 6 in the calibration menu runs the same diagnostic
using the pending calibration, if present, and Ctrl-C returns to the menu.
For slower output, use `factory_calibration.debug(interval_ms=2000)`.
The interval controls both sampling and printing, so 30 readings cover about
30 seconds at the default rate. For a finite CSV capture at the previous faster
rate, use `factory_calibration.debug(interval_ms=100, samples=100, csv=True)`.
Calibration rejection messages also report the observed spread and its limit.


Hardware variants (SW-SPEC-HW-001)
--------------------------------
The same release supports products with and without the ultrasonic sensor.
After stopping the application with Ctrl-C, run `factory_calibration.start()`.
Select **1 Hardware configuration**, select Yes or No, and confirm saving.
Select **2 Water level calibration** to open the existing calibration submenu
(instructions above refer to that submenu). Calibration is Not applicable for
products without the sensor. Select **3 View factory configuration** to check
whether calibration is Required or Completed, then restart with Ctrl-D.

Factory hardware is stored in `factory_hardware.json`, separately from
`factory_water.json` and customer settings. Preserve both factory files during
updates; never erase the filesystem or upload another unit's factory files.
The build does not generate either factory configuration file. Existing units
must explicitly configure their hardware after this update. Missing or invalid
hardware configuration reports a factory configuration fault and prevents sensor
initialization. No variant is assumed. Disabling the sensor retains calibration.
Re-enabling it validates the retained calibration before permitting measurements.

`/api/status` exposes read-only `capabilities.water_level` (true, false, or null
for invalid configuration), `factory_configuration_error`, and `water.state`
(ACTIVE, DISABLED, FAULT). Only false hides the water card. Invalid measurements
remain visible as unavailable and do not produce low-water alarms.

Factory acceptance: restart each variant and check persisted configuration,
EC, temperature, lighting, ambient sensing, and Wi-Fi. For sensor-equipped units,
complete calibration and verify a live measurement; disconnect the sensor and
confirm FAULT with a visible unavailable card. For units without the sensor,
confirm DISABLED, no trigger pulses, no water warning, and no water card.
Also check missing/corrupt hardware configuration and cancellation of edits.
These electrical and on-device checks must be performed on physical hardware.


### Plant EC guide — SW-SPEC-EC-001 revision 1.0

Nutrient alerts includes an optional plant selector with all 14 specified ranges.
The reference table is bundled in `grow.js`; selecting a plant needs no network
request or internet connection. Selection only displays guidance. **Use suggested
range** copies the range into the editable fields and marks the form unsaved.
Only **Save settings** activates and persists thresholds. Users can edit either
field or configure a mixed reservoir without choosing a plant.

The public settings parameters are `ec_alarm_low` and `ec_alarm_high`, in mS/cm.
The existing internal and persistent `ec_low_alarm_us`/`ec_high_alarm_us` values
remain in uS/cm for compatibility with saved settings and physical indicators.
`ec_max_ms` reports the supported upper threshold. The nutrients API includes
`state` LOW, NORMAL, HIGH or FAULT alongside the existing display status.
Threshold equality is NORMAL. Updating guide data never modifies saved limits.

The current analog sensor conversion saturates at **2.72 mS/cm**, its last measured
calibration point. Alert settings are limited to 0–2.72 mS/cm; the prior 100 mS/cm
form limit exceeded that capability. Higher guide ranges remain visible exactly
as specified, and applying them still fills both fields, but the user must adjust
them before saving. The interface explains this limitation. Supporting those
ranges requires a sensor/calibration change, not just a higher form limit.
Existing saved thresholds are not rewritten by this firmware update.

The supplied ranges are indicative starting points for established plants, not
individual nutrient diagnoses. Review the reference values before product
release, as required by the specification. On-device acceptance should include
an offline browser session and EC readings below, at and above saved thresholds,
plus an invalid reading, checking both the nutrient card and physical indicators.


### Navigation and Portal Mode — UI revision 1.2

My Garden provides Settings and Exit Portal Mode. The Settings menu provides
Wi-Fi Settings and Grow Settings, with Back to My Garden.
Wi-Fi setup uses the same local `grow.css` theme and SVG assets, a network list,
manual SSID fallback, password entry and connection status. `wifi.js` makes only
local requests. Wi-Fi and Grow Settings both provide Back to Settings and
Back to My Garden. The dashboard is available even before Wi-Fi provisioning.
Navigation does not save edits. Connecting saves credentials using the existing
storage mechanism and keeps the portal open for status and navigation.

Exit Portal Mode prompts for confirmation. Cancel leaves the portal active;
confirming sends `POST /api/portal/exit` without saving pending form changes.
The server acknowledges the request before starting a two-second grace period.
The browser displays Portal closed and stops polling. Firmware then closes HTTP
and DNS, deactivates the setup AP, and retains the normal STA connection. It
continues into normal runtime even when offline, without resetting the controller
or stopping the pump. The existing physical setup procedure reopens the portal.

`GET /api/portal` supplies navigation availability and Wi-Fi connection status;
`GET /api/networks` scans nearby networks; `POST /api/wifi` accepts form-encoded
credentials. Connection attempts advance between HTTP requests. Network scans
still use the synchronous ESP32 scan API. Static assets are streamed from flash.
The optional legacy Microdot server is not a setup portal and hides portal-only
controls; the active CaptivePortal HTTP server owns Wi-Fi setup and shutdown.


Revision 1.2 corrective verification
------------------------------------
The dashboard, Settings menu and Grow Settings use separate routes and views
served from a shared `settings.html` asset. Wi-Fi setup remains `/wifi`, served
from `index.html`. Every page uses local `grow.css`; no extra HTML assets are
needed. The settings form is hidden on the dashboard and menu. Navigation never
submits or silently saves edits.

The unchanged `/api/networks` contract is `{"networks": ["SSID", ...]}`. It uses
STA WLAN scanning while leaving the setup AP active. Missing scanners, busy
connection attempts and scan exceptions now return an error instead of an empty
success. Zero detected networks is a successful empty list with Rescan guidance.
The browser validates the response and reports malformed JSON, network errors
and a 15-second timeout, including when AbortController is unavailable. Network
names are inserted as text, and credentials still use POST `/api/wifi` with the
existing credential store and connection state machine.

**Hardware acceptance is pending.** Host tests cannot establish that the actual
ESP32 radio can scan and connect with its setup AP active. Revision 1.2 requires
that complete physical provisioning test before this update is accepted.


Connection status correction
----------------------------
Navigating between My Garden, Settings and Grow Settings switches local views
without reloading the shared assets. Hiding a page or opening the exit dialog
pauses polling; it does not report a network loss. Only the dashboard requests
sensor status. Settings views use the lightweight portal endpoint. Responses
from a previous view cannot change the new view's connection status.

See `UI_ACCEPTANCE.md` for requirement coverage and physical-test limits.
