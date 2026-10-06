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


The settings page reads EC from `/adc_value` independently of ambient readings,
with values in µS/cm and visible request errors. Both HTTP server implementations
power the EC probe and allow it to settle before a live reading.


Light schedules use local time in the saved `timezone` setting, defaulting to
`Europe/Stockholm`. `Europe/Lisbon` and `Europe/London` are also supported.
Upload `local_time.py` with the firmware. NTP continues to set the RTC in UTC;
the schedule converts UTC to local time only after the existing NTP sync succeeds.
Before synchronization, the previous hard-coded RTC-hour fallback remains in use.
After synchronization, saved morning/daylight/evening/off boundaries are applied
at minute precision, including schedules spanning midnight.

The Settings page displays the device's local RTC time, or an unavailable message
if the RTC date is uninitialized. The page does not set the RTC or initiate NTP.
Current EU/UK DST rules are implemented in `local_time.py`: transitions at 01:00
UTC on the last Sundays of March and October. These are current scheduling rules,
not a historical IANA database; a future legal rule change requires a firmware
update. At spring-forward, skipped boundaries take effect at the next comparison;
at fall-back, the phase follows the repeated local time. Saved schedule times are
interpreted as local times, without shifting their displayed values.

### GZP6899A water level

The pressure sensor uses GPIO33 (ADC1, 12-bit, 11 dB attenuation). Settings
shows live ADC, depth in millimetres, and calibrated litres. Set tank capacity
and capture four readings at the displayed empty, one-third, two-thirds and
full volumes. Readings are saved after each capture; all four are required
for volume and the LED bar. ADC values must increase with volume.

Depth uses `(ADC - 65.7) / 5.59`, clamped at zero. Volume uses piecewise linear
interpolation through the four saved points, allowing nonuniform tank shapes.
The LED bar indicates the fraction of full volume with up to eight LEDs.
Old ultrasonic calibration is reset automatically. See UPLOAD.md for the
complete precompiled upload set and build instructions.
