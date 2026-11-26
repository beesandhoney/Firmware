EC amplifier calibration update (2026-11-24)
------------------------------------------
The current source uses ADC.read_uv() and piecewise linear voltage calibration:
505/719/820/1274/1588 mV map to 830/1187/1500/2716/3977 uS/cm.
The portal displays mS/cm; the LEDs and alarms use the same calibrated uS/cm value.
The supported threshold maximum is now 3.977 mS/cm. Existing thresholds are retained.
Readings above the last reference are capped; accuracy beyond it is unverified.
These changes do not change production GPIO assignments or EC power control.
Rebuild the firmware and use a matched deployment set. Existing frozen images,
including the image referenced by FLASH_FROZEN.md, do not contain this calibration;
uploading Python or bytecode cannot update their frozen modules.

Grow consumer interface (SW-SPEC-UI-001 revision 1.0)
==================================================

2026-11-07 status: the routing-3 association log still shows native exhaustion
before HTTP. The reported `python_free` includes memory available for GC heap
expansion; it is not independent Python-only free space. See
`MEMORY_DIAGNOSIS.md` for the corrected calculation and proposed frozen-firmware
direction. No current candidate is accepted as a memory fix.

Portal routing correction — portal-routing-3 (2026-11-06)
--------------------------------------------------------

For a device running the matched portal-memory-2 set, upload only
`_fw_captive_http.mpy`, then restart. The main banner remains portal-memory-2;
HTTP startup must also print `HTTP build: portal-routing-3`. Build hashes confirm
only this compiled module changed; pages and the other modules are unchanged.

The latest physical log shows repeated successful page/API responses followed by
`/settings` stalling with native free/largest 2796/1152 bytes. Later sends stall at
zero bytes with native free/largest 2576/1152. The memory-2 candidate has therefore
not resolved the low-memory problem.

Missing browser icon requests were redirected to `/`, causing unnecessary full
dashboard transfers. Unknown paths now receive an empty 404; the existing explicit
captive-detection routes still redirect. Socket-close exceptions are now logged
as `HTTP close failed` instead of silently discarded. A close failure has not yet
been observed; that change exposes a diagnostic gap, not a proven socket leak.
Backlog, scan behavior, page content and retry budgets are unchanged.

Host validation: 57 relevant tests pass, including actual request dispatch for
missing icons and captive probes, streaming, and failed-close logging/cleanup.
Physical validation is pending: enter setup mode after restart, repeat My Garden
→ Wi-Fi → Back to Settings/My Garden, and retain the full serial log. Icon requests
should now receive small 404 responses without causing extra home-page transfers.
Compare successful navigation, native heap readings, retry-limit and close-error
events. This fixes unnecessary traffic; it is not yet proof of adequate Wi-Fi RAM.

Portal memory candidate — portal-memory-2 (2026-11-06)
----------------------------------------------------

For a device running the HTTP diagnostic build below, upload these four files
together, then restart: `main.py`, `_fw_app_main.mpy`, `_fw_captive_portal.mpy`,
and `_fw_captive_http.mpy`. Expect `Firmware build: portal-memory-2` on serial.
The previous single-file diagnostic upload is not sufficient for this candidate.

The earlier precompiled-loader/GC mitigation remains in place. Keeping lighting
active in the portal had introduced a call to the full runtime loader, importing
NTP and uasyncio during AP startup. This candidate loads GPIO/settings for portal
lighting and defers NTP/uasyncio until normal operation. UI, socket backlog and
retry budgets are unchanged to isolate this correction.

`MEM` lines pair Python allocated/free memory with the following native heap
report before/after control loading, at portal startup/station changes, and at
the first send stall. Logging itself allocates; these are sampled observations,
not exact native-allocation traces. Native heap exhaustion is confirmed in the
previous device log; this dependency change is not yet a proven complete fix.

Host validation: 54 tests pass, including portal lighting without NTP/uasyncio,
transition to normal runtime, HTTP streaming/recovery and navigation.
Physical acceptance: capture a cold boot, join GrowWiFiSetup, navigate between
My Garden and Wi-Fi repeatedly, disconnect/rejoin and repeat. Retain the complete
log including build label, MEM/native heap, HTTP TX and any driver errors. Require
successful responses and no driver out-of-memory errors before accepting it;
compare Python allocated and native free/largest readings with the earlier build.

HTTP diagnostic build (2026-11-06)
---------------------------------

For a device already running the preceding workspace build, upload the rebuilt
`_fw_captive_http.mpy`, then restart. The matching source is `captive_http.py`;
uploading that source alone does not update the running compiled module.

Keep serial monitoring attached, enter Device Portal with the two-second press,
open http://192.168.4.1/, then navigate to Wi-Fi. Capture `HTTP TX` lines:
`path`, `event`, `errno`, `sent`, and `elapsed_ms`. `sent` includes headers and
counts bytes accepted by the socket, not confirmed browser receipt. Timing starts
at response writing, excluding request reads and route processing. A `complete`
event means the full response was queued to the socket.

`backpressure` records the first temporary send error per response; `retry-limit`
records exhaustion of the unchanged 300-attempt budget. `socket-error` records a
non-retryable error, including reset, aborted connection, or broken pipe, and
ends that response immediately. Query strings and submitted credentials are not
logged. Page design and retry delays are unchanged. This is diagnostic firmware,
not a confirmed fix for the underlying AP delivery problem.

The complete upload set below includes the new local interface and the GPIO32
NTCG163JF103FT1H temperature driver (10 kOhm pull-up to 3.3 V). Upload both the
web assets and all compiled modules together. Copying only settings.html will
leave the page without its styling, behavior, icons and API support.

Open the device IP in a browser, or join its access point. My Garden is now the
home page; /settings opens the same page and the footer links to Wi-Fi setup.
The local HTTP server also runs during normal operation on the home network.
Use Save settings to commit changes. Settings and factory_water.json are retained
across this update; do not erase or replace either file.

On the device, check the four status cards, save and restart to confirm settings
retention, and compare temperature with a reference thermometer. Browser previews
and host tests use simulated sensors; they do not establish physical accuracy or
ESP32 memory/network stability under load.

The current build retains precompiled startup to reduce native networking memory
pressure and restores local-time scheduling with automatic DST. Stockholm is the
default; Lisbon and London are also supported. NTP continues to use UTC.

Upload these files from this directory to the ESP32 root, then POWER-CYCLE:

- main.py
- firmware_loader.py
- all sixteen _fw_*.mpy files
- settings.html, index.html, connected.html, grow.css, grow.js and wifi.js
- icons/ with all seven SVG files
- factory_calibration.py
- hardware_config.py
- water_config.py
- volume_http.py

Keep the other existing source files and device
settings on the ESP32. The compiled loader explicitly selects _fw_ modules;
old .py files do not override them. No filesystem erase is needed.

Expect `Firmware build: portal-memory-2` on serial startup. Compare the
Network heap free/largest values after a phone associates and check whether the
phone now receives a 192.168.4.x address. Host tests cannot verify DHCP delivery.

Source for the application formerly in main.py is now app_main.py. Other source
files keep their names. After any firmware-source edits, regenerate bytecode:

    python3 build_firmware.py --compiler /path/to/mpy-cross

Use mpy-cross v1.28.0. Compiler package used: mpy-cross==1.28.0.post2.
firmware_build.json records source and bytecode hashes so stale builds can be
detected. Upload the entire generated set after rebuilding. Host-side build
scripts, manifests, tests and this document do not need uploading.

Water calibration now implements SW-SPEC-WL-001 revision 1.1: empty (0%) and
maximum recommended level (100%), with version-2 persistent data. It derives
effective height from the two measured distances; no tank-height entry is needed.
This update changes the runtime calculation as well as the REPL procedure.
Upload the complete set above, including the rebuilt _fw_water_config.mpy and
_fw_volume_http.mpy. Updating only factory_calibration.py is insufficient.

The effective-height minimum is now strictly greater than 50 mm (previously
100 mm). For this limit change on an otherwise current device, upload both
water_config.py and _fw_water_config.mpy, then restart with Ctrl-D:

    mpremote fs cp water_config.py _fw_water_config.mpy :

If calibration reports `AttributeError: 'module' object has no attribute 'create'`,
the loaded water_config module is older than the factory script. Startup aliases
_fw_water_config.mpy as water_config, so even a newer water_config.py cannot
replace already loaded bytecode. Upload both source and bytecode, then restart;
repeating `import factory_calibration` does not refresh cached modules. The
updated factory script checks compatibility before taking either measurement.

If normal operation reports `calibration=fault`, the accepted calibration is
missing or invalid in the runtime. Accepting the two measurements only creates
a pending calibration: choose menu 4 Save and confirm, then check for
`Factory configuration saved.` before restarting. The current build reports
load failures at startup and includes the reason as `error=` in WATER logs.
To inspect an existing device without changing its calibration, stop with
Ctrl-C and paste this single line at the REPL:

    import water_config; print(getattr(water_config, 'FORMAT_VERSION', 'old module')); print(water_config.current()); print(open('factory_water.json').read())

This displays the loaded format version, cached configuration, and saved file.
An ENOENT error means the file is missing. Keep the output when investigating
a fault instead of overwriting the existing calibration.

From the Mac terminal, for a device already running this project's boot loader:

    mpremote fs cp main.py firmware_loader.py factory_calibration.py hardware_config.py water_config.py volume_http.py settings.html index.html connected.html grow.css grow.js wifi.js _fw_*.mpy :
    mpremote fs cp -r icons :
    mpremote repl

The trailing `:` selects the ESP32 filesystem. Press Ctrl-D to restart, then
Ctrl-C to stop the application. At the ESP32's `>>>` prompt, run:

    import factory_calibration
    factory_calibration.start()

Choose 1, enter the bottom diameter with the reservoir empty, then enter the
diameter at maximum recommended fill after filling to that level. Review the
calculated distances, effective height and maximum volume. Optional intermediate
verification is menu 3; save with menu 4 and exit with menu 5. Each reference uses
30 samples and a 10 mm spread limit. Failed points can be retried individually.

Keep factory_water.json on the device across firmware updates; do not overwrite
it with another unit's data. Old version-1 and four-point calibrations are not
compatible with the new reference conditions. They produce a configuration fault
until a new empty/full calibration is saved. The previous file is preserved
until the operator confirms saving a valid replacement.

Run factory_calibration.debug() to see labelled readings and rolling spread
about once per second; Ctrl-C stops the diagnostic. Use interval_ms=2000 for a
two-second pause between readings. The same diagnostic is menu 6.


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


Plant EC guide update
---------------------
Upload `settings.html`, `grow.js`, `grow.css`, and the complete rebuilt `_fw_*.mpy`
set together. No plant data file or external service is needed. Refresh the
browser after uploading. Preserve `settings.json` and both factory JSON files.
The guide does not migrate or overwrite saved EC thresholds. New saves enforce
the current sensor's 2.72 mS/cm upper limit; suggestions above it are displayed
with guidance to adjust the values before saving.


Navigation and Portal Mode update (UI revision 1.1)
--------------------------------------------------
Upload both HTML pages and the new `wifi.js` together with `grow.css`, `grow.js`,
all generated bytecode, and `main.py`/`firmware_loader.py`. Preserve the existing
Wi-Fi credentials, customer settings and factory files. No filesystem erase is
needed. Refresh the browser after uploading to load the shared theme.

On-device acceptance: test initial setup and configured-device entry; scan and
connect to a network; verify progress, wrong-password retry and Back to My Garden.
Cancel an exit, then confirm one with unsaved edits. Verify the completion screen,
AP/server shutdown, retained normal Wi-Fi connection, continued lighting/pump/EC
operation, and no reset. Repeat exit without a normal Wi-Fi connection. Reopen
using the existing physical setup control. Check both pages on a narrow mobile
screen without internet access. A network scan uses the ESP32's synchronous scan
API and may briefly delay responses; connection attempts are serviced in steps.


Corrective navigation update (UI revision 1.2)
---------------------------------------------
Upload `settings.html`, `index.html`, `grow.js`, `wifi.js`, `grow.css`, and all
16 rebuilt `_fw_*.mpy` files together. Retain `main.py`, `firmware_loader.py`,
`connected.html` and the local `icons/` folder from the previous update. Preserve
all unit-specific settings and credentials. Restart and refresh the browser.
No additional HTML files are required: the dashboard, menu and Grow Settings
are route-specific views in the shared HTML asset.

Required physical acceptance (not yet completed):
1. Open My Garden, then Settings. Verify both menu choices work.
2. Open Wi-Fi Settings with the setup AP active. Confirm real nearby SSIDs appear.
3. Rescan, select a network, enter credentials and connect. Confirm success and
   both back links. Test a failed connection and retry as well.
4. Open Grow Settings, save a change, reload and verify persistence.
5. Cancel Portal Mode exit, then confirm it. Check continued controller operation,
   AP/server shutdown, and reopening with the physical setup control.

The ESP32 must pass the complete provisioning flow before revision 1.2 is accepted.
Host tests or a visually correct page alone do not satisfy this requirement.
