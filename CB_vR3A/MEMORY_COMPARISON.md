# ESP32 portal memory comparison

This procedure compares filesystem-loaded bytecode with the frozen-module build
using the same application sources, web pages, MicroPython version and ESP-IDF
configuration. It is a physical acceptance test; host unit tests do not establish
ESP32 memory stability or reliable Wi-Fi association.

## Keep the comparison controlled

Record the baseline and candidate build identities and manifests before testing.
Install the complete matched application package, including `main.py`,
`firmware_loader.py`, and all 17 `_fw_*.mpy` modules (including
`_fw_memory_probe.mpy`) for the filesystem baseline. The frozen candidate requires
its matching ROM image and bootstrap files. The package manifest must verify
bootstrap hashes as well as application modules; a new `main.py` with an old
module set is not a valid baseline. The frozen build must report `FW_STORAGE
frozen` and its `FW_SOURCE` identity; the baseline must report `FW_STORAGE
filesystem`. Follow the build/package installation instructions before this test.

The source hashes, page hashes, AP configuration, HTTP backlog and retry settings
must match between builds; only module storage/loading should differ. Retain the
saved Wi-Fi, hardware and calibration settings. Use the same ESP32, power supply,
client, location and network conditions. Do not change them between runs.

Capture serial continuously to separate files for each build/run. Look for the
build banner and `MEM_BEGIN`, `MEM_NATIVE`, and `MEM_END` samples with their phase
and device ticks. These contain `micropython.mem_info()` details as well as native
heap readings. Keep incomplete samples and driver errors in the original log;
do not discard failed runs. A filename or workload `--build-label` is an operator
annotation, not proof of which firmware is running.

Perform a genuine power-off/on cold boot for each run. A Ctrl-D soft reboot can
retain different native/network state and is not an equivalent baseline. Start
serial recording before the normal boot, then use the existing two-second button
press to enter Device Portal. Keep monitoring attached during the resulting
firmware-controlled restart. Do not connect a second serial program.

## External computer workload

Join that computer to **GrowWiFiSetup** and verify that it receives a usable
`192.168.4.x` address. Keep only that client on the AP; this firmware permits one
setup client. Close browser tabs to the device during the scripted run so their
polling and icon fetches do not add uncontrolled traffic. Do not substitute the
home-network address for the AP address in the same comparison.

Run from the repository root, saving stdout as JSONL:

```sh
python3 tools/portal_memory_workload.py --url http://192.168.4.1/ --build-label baseline-filesystem --cycles 3 --timeout 8 --concurrency 1 --stage-delay 0.25 > baseline-sequential.jsonl
```

After installing the candidate and repeating the same cold-boot/join sequence:

```sh
python3 tools/portal_memory_workload.py --url http://192.168.4.1/ --build-label candidate-frozen --cycles 3 --timeout 8 --concurrency 1 --stage-delay 0.25 > candidate-sequential.jsonl
```

The fixed sequence fetches My Garden, sensor status, missing browser icons,
Settings, Grow Settings, saved settings, Wi-Fi, the network scan, portal status,
and My Garden again. The scan is a deliberate read-only GET and temporarily
activates the Wi-Fi scanner; the script never posts credentials, changes settings,
exits the portal or follows redirects. Empty 404 responses are expected for the
missing icons. Any redirect, failed request or incorrect response is recorded as
a failure instead of being retried or hidden.

Each request uses a fresh connection and validates status, content type,
Content-Length completeness and the expected JSON structure. The timeout covers
the headers and the whole response, including a peer that keeps trickling bytes.
Responses are capped at 256 KiB. JSONL includes request times, byte counts, fixed
error labels and HTML hashes; it never records response bodies, scanned SSIDs,
saved SSIDs or credentials. The summary has `ok: true` only when every check
passes, and the process exits with status 1 if any check fails.

A three-cycle run makes 45 requests. With these settings the conservative timeout
budget is approximately six minutes, plus small scheduler/connection-cleanup
costs; successful runs finish much faster. Inspect the flushed JSONL and serial
output while it runs. An interrupted run lacks a final summary and must not be
counted as a pass.

For an optional concurrency comparison, repeat **both** builds from cold boot
with `--concurrency 2`, retaining all other arguments and using different output
filenames. This overlaps requests only within status/icon stages; navigation and
network scanning retain stage order. Do not compare a sequential baseline with a
concurrent candidate. The metadata records the actual workload settings and plan.

## Reconnection and real-browser checks

After the scripted run, disconnect the same client from GrowWiFiSetup, wait ten
seconds, then reconnect without restarting the ESP32. Repeat the identical
workload and save it under a `rejoin` filename with a matching serial log. Repeat
this pairing for both firmware builds. Record inability to join or obtain an IP
as a failure even when no HTTP request reaches the device.

Test the phone separately: disconnect the computer from the AP, cold boot the
ESP32, enter Device Portal, then join with the same phone for each build. Open
`http://192.168.4.1/` in a normal browser and navigate My Garden → Settings → Grow
Settings → Settings → Wi-Fi → My Garden three times. Record the page/step and
visible result if it stalls, shows Connection lost, or fails to rejoin. The
automatic captive-portal window is an additional separate test, not a substitute
for the normal-browser run. Keep the full serial record for each.

A successful device-local loopback request does not validate AP transmission,
association or DHCP. A successful computer workload validates external HTTP on
that connection, but does not validate phone rendering, browser history behavior,
or OS captive-portal behavior. Keep these results distinct.

## Interpret results

Use `tools/compare_memory.py` for the serial-memory comparison according to its
`--help`, and retain the raw serial files alongside the HTTP JSONL. Compare like
phases: boot/module load, AP startup, association, HTTP workload, scan and rejoin.
Review existing GC heap capacity/free, largest GC free block, native free/largest
and native low-water marks. On this ESP32 build `gc.mem_free()` includes memory
that could be added from native heap; it is not by itself a native-memory reserve.

Require complete HTTP runs, successful repeated association/rejoin and browser
navigation, no driver out-of-memory errors, and materially improved native
headroom without a growing GC heap consuming that improvement. Do not declare a
fix from one initial page load, an average that hides failures, or a host test.
If either run fails, retain its exact phase and diagnostics before changing more
variables. A changed source/asset/configuration invalidates attribution to module
freezing alone and needs a new matched baseline.
