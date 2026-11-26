# Grow UI revision 1.2 — verification record

Date: 2026-11-03. Full acceptance remains pending the complete physical provisioning flow.

| Requirement group | Implementation | Verification |
|---|---|---|
| NAV-001–002 | Dashboard with Settings and confirmed Exit Portal Mode | Host route/view tests; device serves dashboard |
| NAV-010–013 | Settings menu links to Wi-Fi and Grow Settings; Back to My Garden | Host route/view tests; device serves menu |
| WIFI-001–007, 009–012 | Real STA scan with AP active; selection; credential POST; progress/error/result; Rescan; explicit failures | Physical scan found 3 networks; real credential POST and timeout; rescan recovery passed |
| WIFI-008, SET-002 | Both subpages link to Settings and My Garden | Route/view tests; device serves destinations |
| SET-001, 003 | Existing lighting, ambient and EC form; validated transactional saves | Host persistence, reload, invalid-input and failed-save tests |
| THEME-001–004 | Shared local Grow CSS, components and SVG assets | Static asset tests; pages/CSS/JS streamed successfully by ESP32 |
| PORTAL-001–003 | Confirmation; POST acknowledgment; delayed HTTP/AP shutdown | Host tests and physical HTTP exit acknowledgment/shutdown passed |
| PORTAL-004–005 | Portal-only cleanup; retained STA; common normal-runtime startup; existing physical setup procedure | Host lifecycle tests; physical normal-operation/reopening check pending |
| IMP-001–006 | Existing scanner and credential backend, local routes/assets, bounded requests, incremental connection attempts | Host tests and partial device provisioning tests |

## Corrections found during physical testing

- A timed-out ESP-IDF association continued in the driver and caused a later scan
  to return no networks. The timeout handler now disconnects that failed attempt
  while retaining the AP. Repeated real HTTP provisioning failure/rescan tests pass.
- The frontend previously marked `visibilitychange` to hidden as Connection lost.
  It now pauses polling without inventing a network failure.
- Dashboard, Settings menu and Grow Settings navigation now changes views using
  local history navigation. It does not reload assets or save the form.
- Settings/menu polling no longer requests sensor status. Pending responses are
  tied to their originating view. Exit confirmation pauses polling; late failures
  cannot replace the confirmation or the successful completion message.

## Test coverage and limits

98 Python host tests and JavaScript suites cover validation, navigation, offline
assets, timeout handling, scan failures, cancellation and acknowledged exit.
Physical HTTP tests used loopback sockets on the actual ESP32 with its setup AP
active; they establish the real backend and radio behavior, not browser rendering
or DHCP from an external client. No user password was printed or copied to logs.

The old saved Wi-Fi network was not present in the scan. Successful provisioning
therefore needs new credentials entered by the owner through the setup page.
The device is running its normal application in GrowWiFiSetup mode with temporary
serial status observation. Browser navigation/exit confirmation, successful
connection, physical operation after exit, and button reopening remain to be
confirmed before declaring revision 1.2 accepted.


## Browser asset delivery regression — still awaiting external confirmation

Real browser testing reported missing CSS/SVGs and intermittent navigation. Serial
logs confirmed repeated response-write failures for `grow.css`, `grow.js`,
`wifi.js`, and icons. The original sequential loopback test was insufficient to
validate browser concurrency and radio delivery.

The server now queues six connections and uses nonblocking socket `send()` with
explicit partial-progress handling and bounded retries. It no longer reuses a
short request-read timeout for stream writes. Tests cover partial sends,
backpressure lasting longer than the old retry budget, and bounded stalls.
The shared Grow theme and artwork have not been replaced or simplified.

The transport fix has been uploaded. A separate-computer test is prepared to
fetch CSS, JS and SVGs using six concurrent connections and compare every byte
against the workspace assets. It remains blocked on a working normal Wi-Fi
connection (the currently saved credentials returned FAIL_AUTH). User visual
confirmation is pending; do not mark the UI accepted based on host tests alone.

### Lighting amendment SW-SPEC-UI-001 revision 1.3

- Configure Morning, Day, Evening and Night start times; confirm the three sliders show whole percentages and Night is fixed OFF.
- Save 05:00/40%, 07:00/100%, 19:00/50%, 21:00/OFF. Restart and confirm all values persist. Unsaved edits must not affect outputs.
- Verify each boundary using configured local time, including a schedule crossing midnight. Duplicate or out-of-order phases must be rejected without changing saved settings.
- Set a daytime phase to 0% and confirm physical light output is OFF.
- Confirm My Garden refreshes phase, actual commanded brightness, fan duty and next phase without reloading. Unavailable outputs and lost connections must not retain misleading values.
- GPIO25 currently has binary fan control: verify reported 0% and 100% match the existing threshold logic. Fan status is independent of the displayed phase and is not RPM or airflow.
- Verify ambient dimming reports the resulting light output and manual mode identifies the override rather than claiming a scheduled phase.

Host validation: Python schedule, persistence, API and output-command tests plus JavaScriptCore form/status tests. Physical PWM and restart checks require the ESP32.
