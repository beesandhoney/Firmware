The current build retains precompiled startup to reduce native networking memory
pressure and restores local-time scheduling with automatic DST. Stockholm is the
default; Lisbon and London are also supported. NTP continues to use UTC.

Upload these files from this directory to the ESP32 root, then POWER-CYCLE:

- main.py
- firmware_loader.py
- all twelve _fw_*.mpy files
- settings.html

Keep index.html, the other existing source files, and device
settings on the ESP32. The compiled loader explicitly selects _fw_ modules;
old .py files do not override them. No filesystem erase is needed.

Expect `Firmware build: precompiled-pressure-water-debug-3` on serial startup. Compare the
Network heap free/largest values after a phone associates and check whether the
phone now receives a 192.168.4.x address. Host tests cannot verify DHCP delivery.

Source for the application formerly in main.py is now app_main.py. Other source
files keep their names. After any firmware-source edits, regenerate bytecode:

    python3 build_firmware.py --compiler /path/to/mpy-cross

Use mpy-cross v1.28.0. Compiler package used: mpy-cross==1.28.0.post2.
firmware_build.json records source and bytecode hashes so stale builds can be
detected. Upload the entire generated set after rebuilding. Host-side build
scripts, manifests, tests and this document do not need uploading.

Water level: connect the GZP6899A signal to GPIO33. The firmware reads 12-bit
ADC values at 11 dB attenuation and averages 20 samples, 5 ms apart.
In Settings, enter the full usable tank capacity and press Start / reset
calibration. Record the four displayed volumes in order, waiting for the
water to settle at each point. All four readings persist across restarts.
Legacy ultrasonic calibration is discarded; its tank capacity is retained.
The eight LEDs show calibrated volume / capacity (empty is off, full is eight).
Incomplete or failed measurements clear the bar. Depth is shown in mm using
the supplied ADC offset 65.7 and slope 5.59; the volume table interpolates
between measured points and clamps outside the calibrated range.

Pressure timing diagnostics are enabled with WATER_DEBUG in app_main.py.
Normal mode prints WATER lines with ADC, depth (mm), volume (L), calibration
state, completion-to-completion gap, and sampling/read duration. A normal gap
is about 600 ms and a read about 95 ms. TIMING lines report NTP blocking and
other runtime stages taking at least 250 ms. NTP remains synchronous in this
diagnostic build; failed attempts now wait 30 seconds after completion before
retrying. Capture serial output across a slow update to identify the blocker.

The WATER line now also reports capacity and LED_command (0–8). This is the
command sent synchronously before printing, not feedback from the LEDs. None
means the indicator is unavailable or the write failed. Compare volume and
LED_command during a pressure change: a steady count indicates a calibration
or step-threshold effect; a changed command with an unchanged physical bar
points to the output path. Current mapping floors volume * 8 / capacity, with
one LED for any positive volume, so one LED spans greater than 0 to below 25%.
