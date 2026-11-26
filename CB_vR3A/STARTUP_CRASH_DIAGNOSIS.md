# Startup crash investigation

> Device validation update (2026-11-08): GC expansion during GPIO import and
> the subsequent ADC allocation failure are now confirmed. The matching ELF
> resolves the abort to logging-mutex allocation failure. See
> [measured Phase 1 results](diagnostics/startup-validation-2026-11-08/RESULTS.md).
> The investigation below records the earlier, pre-validation findings.

## Findings and limits

The strongest hypothesis is automatic GC split-heap growth during `load("gpio")`,
followed by a native allocation failure. This is not yet a decoded abort or a
confirmed root cause. One exhausted heap region does not establish that every
native capability pool is exhausted. No application ELF, map, complete panic
log, or device checkpoint capture was available in this workspace.

The supplied region loses 43,012 free bytes; its largest block shrinks by 42,932
bytes. Python live allocation rises by only 4,128 bytes. On this ESP32 version,
`gc.mem_free()` combines existing GC free space and potential new split space;
it is not an independent Python reserve. Assuming this largest data block was
also the block eligible for GC growth, the approximate existing GC free values
are 49,104 - 43,008 = 6,096 before, and 43,964 - 76 = 43,888 after. Approximate
GC capacity therefore rises from 112,000 to 153,920 bytes (41,920 bytes).
The remaining difference is consistent with split metadata/allocator costs.
This assumption must be checked using the directly printed `GC: total` and
`max new split`, rather than treating subtraction across capability pools as
an exact measurement. A temporary contiguous import allocation can trigger this
expansion even when collection leaves relatively little additional live data.

## Startup and side effects

1. `boot.py` constructs STA/AP WLAN objects, disconnects/disables them, waits
   200 ms, collects, and drives pins 15/18/23 low. WLAN construction can initialize
   native network resources even though both interfaces are then disabled.
2. `main.py` collects and sets `gc.threshold(4096)`. This threshold controls
   collection frequency, not native reservation or a maximum Python heap size.
3. The loader checks the frozen marker. `main.run()` preloads `memory_probe`,
   then status_led, credentials, local_time, hardware_config, water_config,
   shared_settings, level_indicator, water_level, temperature, grow_ui,
   volume_http, pump_cycle, captive_portal, captive_http, in that order.
   It loads configuration files and then app_main. Filesystem `.mpy` avoids
   source compilation but still puts bytecode and objects into RAM.
4. Importing app_main constructs `CaptivePortal` (including `Creds`) and the
   GPIO26 startup button. CaptivePortal's constructor does not create WLANs.
5. `app_main.run()` initializes status LEDs, waits through the boot grace,
   initializes the pump, and starts either the settings portal or normal Wi-Fi.
   During synchronous Wi-Fi waits, `_service()` calls `service_startup_controls`,
   which calls `service_portal_lighting` and thereby `load_control_modules`.
6. Exactly between the original control-module markers: loader import;
   `load("gpio")` (collect, import `_fw_gpio`, alias in sys.modules, collect);
   import five existing shared_settings symbols; assign six module globals;
   collect. No asyncio task creation, explicit Wi-Fi initialization, ADC creation,
   PWM creation, or sensor initialization occurs in that interval.
7. GPIO import does create EC power pin 5 (off), fan pin 25, button inputs
   26/17, LevelIndicator with output pins 4/14/22 and an eight-bit clear sequence,
   and output pins 18/23 (off). PWM and water sensing remain lazy.
8. Immediately after control loading, portal lighting constructs
   AmbientLightController (ADC34) and ticks it; a positive brightness can create
   two PWM objects. These are plausible first native allocation failures after
   the observed low-memory marker and are separately bracketed where practical.
9. NTP/uasyncio load later; runtime hardware creates the lock, ADC35, settings,
   ambient controller and button detectors. Three tasks are scheduled only after
   Wi-Fi/portal startup and runtime hardware initialization complete.

The inspected control-import dependency graph has no circular edge back to
app_main. The gpio sentinel prevents repeated successful control-module loads;
ambient_controller and runtime_initialized similarly guard normal repeated calls.
No callback from the GPIO/LevelIndicator constructor invokes startup again.
The Wi-Fi service callback chain returns through lighting/ADC/PWM operations;
those operations do not call Wi-Fi service again. No unbounded application
initialization recursion was found. This does not rule out C stack overflow in
unobserved paths or the exact firmware running on the device.

## Instrumentation

`memory_probe.report` retains `MEM_BEGIN`/`MEM_NATIVE`/`MEM_END` compatibility.
`micropython.mem_info()` prints actual GC capacity/used/free, prospective split
size, and supported stack used/limit fields. Every HEAP_DATA region is printed
as index, total, free, largest, minimum-free watermark. No formatted JSON,
per-sample dictionaries, forced GC, or stored sample history is added by the
probe. Existing loader collections remain in place, with separate checkpoints
before import, after pre-import GC, after module execution, and after final GC.
Cached loads are not logged repeatedly. Individual startup operations use the
existing portal diagnostic hook. Boot Wi-Fi reset precedes probe preloading;
the first startup sample includes its retained effects.

Verified against local v1.28 source: `HEAP_DATA = MALLOC_CAP_8BIT` (1 << 2),
`HEAP_EXEC = MALLOC_CAP_EXEC`; GC's `gc_get_max_new_split()` uses
`MALLOC_CAP_DEFAULT` (1 << 12). These masks are different. Use the symbolic
HEAP_DATA constant and all returned regions; do not sum overlapping DATA/EXEC
views or assume one tuple represents all native memory.

Optional `load('memory_probe').tasks()` at the REPL reports FreeRTOS task stack
watermarks when `idf_task_info` exists (requires the trace facility build option).
It allocates task/result objects, so is not called at every checkpoint. The
regular mem_info stack sample is a current measurement at checkpoint depth,
not a historical FreeRTOS minimum or proof that a deeper transient stack peak
was safe. Logging itself allocates and can move the expansion threshold; compare
both instrumented and ordinary runs after identifying the failing operation.

## Decode the abort before selecting a correction

The application label `portal-frozen-comparison-1` is not a unique native ELF
identifier. Obtain the ELF produced with the running base firmware and match its
app ELF SHA256 from the full panic/build provenance. The local
`.firmware-build/releases` contains an application upload ZIP, not a matching
MicroPython ELF. Rebuilding the same version with other flags is insufficient
for address-level conclusions.

Using that matching ELF and the ESP32 Xtensa toolchain:

```sh
xtensa-esp32-elf-addr2line -a -f -C -i -e /path/to/micropython.elf \
  0x40082f9b 0x40106591 0x4010665b 0x40085816 0x400febc3
```

Decode every PC from the complete backtrace, preserving order and repeats;
retain PC:SP pairs, assertion/stack-canary messages, registers, ELF hash and
all lines immediately preceding abort. `idf.py monitor` with the matching
build is an alternative. Do not decode SP values as program counters.

`abort()` does not itself distinguish malloc assertion, stack protection,
heap corruption or other assertions. The ESP32 GC's `gc_collect_inner` recurses
a bounded number of times to spill Xtensa register windows; repeated frames
could therefore be legitimate GC internals. Interpreter frames can also repeat
normally. Only decoded frames plus stack evidence can resolve this.

## Smallest correction to evaluate

No functional initialization or allocator policy change is made in this task.
If checkpoints confirm GC capacity growth alongside loss of native space,
first evaluate the existing frozen-bytecode build path: it reduces Python RAM
residency without changing controller behavior. Avoid retaining unnecessary
preloaded modules if a smaller, dependency-safe omission suffices, but do not
remove prerequisites blindly because the loader aliases compiled module names.
Do not claim freezing alone fixes a decoded stack overflow.

A custom native reserve or GC growth cap needs a measured reserve and sufficient
Python capacity; by itself it can replace an abort with MemoryError. More
frequent collection or `gc.disable()` is not a reliable native reservation fix.
If decoding instead exposes recursive initialization, correct that specific
cycle/reentrant callback; increasing stack size alone is not a recursion fix.

Rebuild generated `_fw_*.mpy` and manifest before upload; editing app_main.py
alone does not change the filesystem-loaded application. Capture a cold startup
and soft reset, then verify AP/STA connection, lighting and pump operation,
association, scanning, navigation and reconnects. Require bounded stack use,
no abort, and native largest/free margin under the complete workload.

## Primary references

- https://docs.micropython.org/en/v1.28.0/library/esp32.html#esp32.idf_heap_info
- https://docs.micropython.org/en/v1.28.0/library/micropython.html#micropython.mem_info
- Local upstream source: `.firmware-build/micropython/py/gc.c`,
  `ports/esp32/gccollect.c`, `ports/esp32/modesp32.c`.
- Local IDF source: `.firmware-build/esp-idf/components/heap/include/esp_heap_caps.h`.
