# ESP32 startup validation — 2026-11-08

Phase 1 measurements captured. No base-firmware image has been flashed.
The user confirmed the first capture was not a physical power cycle; its
POWERON_RESET marker is therefore classified as a hardware-reset run. The physical-cold-boot capture subsequently detected a USB disconnect,
reconnected, and captured a complete new POWERON_RESET boot with the same
measurements and ADC abort. The power-off/on run is the second POWERON_RESET
in filesystem-physical-cold-boot.log (following a pre-disconnect partial run). A separate Ctrl-D test includes a confirmed MPY soft-reboot marker.

## Recovery and deployment preparation

- Device responds over `/dev/cu.usbserial-140`: MicroPython v1.28.0 dated
  2026-04-06, ESP32_GENERIC, portable bytecode version 11014.
- Existing 4,194,304-byte recovery image at
  `.device-backups/2026-11-08-before-frozen/flash.bin`: SHA-256 and all saved
  chunk hashes verified. This is an earlier recovery snapshot, not proof that
  the current device flash is byte-for-byte identical. A subsequent read-only
  hash of the live factory application partition did verify that its complete
  2,031,616 bytes match the saved recovery application partition: SHA-256
  `afc8f6ff833b58822ee1e6633e5bd89a4402da0a977ba2d108e1977e53ae186e`.
  Thus the current base firmware is preserved, alongside the fresh logical
  filesystem backup; a complete current raw-flash snapshot has not been made.
- Fresh pre-deployment filesystem copy, all 102 files verified against device-side SHA-256:
  `.device-backups/2026-11-08-startup-validation/filesystem/`.
- Local source/bytecode manifest verified. Added diagnostic-only checkpoints
  immediately around ambient ADC and first lighting PWM creation; initialization
  order, arguments and control policy remain unchanged. Rebuilt all 17 modules.
- 49 relevant host tests pass. These do not establish hardware reliability.

## Proposed Phase 2 configuration (conditional on Phase 1 evidence)

Use the existing paired-build workflow, with an immutable source snapshot shared
by the baseline and candidate:

| Setting | Proposed value |
| --- | --- |
| Board | ESP32_GENERIC, default dual-core variant, no PSRAM |
| MicroPython | v1.28.0, commit e0e9fbb17ed6fd06bb76e266ae554784c9c80804 |
| ESP-IDF | v5.5.1 |
| Build environment | espressif/idf:v5.5.1 pinned to its actual Docker image ID |
| Baseline manifest | Upstream boards/manifest.py |
| Candidate manifest | Same upstream manifest plus freeze("frozen") |
| Application | All 17 `_fw_*` modules, including memory_probe and gpio |
| Frozen identity | `_grow_frozen_build.BUILD_ID` from source snapshot hashes |
| Application slot | Existing factory slot, offset 0x10000, length 0x1f0000 |
| Preserved filesystem | Existing bytes from 0x200000 to 0x400000 |
| Runtime configuration | Same credentials, hardware settings and calibration |
| Build artifacts | Both ELF/map files, application binaries, hashes and SDK configuration |

Keep the current allocator and stack configuration unchanged for the comparison.
The loader's frozen path restricts imports to `.frozen`, verifies ownership of
both aliases, and must report `FW_STORAGE frozen` and matching `FW_SOURCE`.
Filesystem copies must not substitute for missing frozen modules. Verify exact
source and bootstrap hashes against the candidate before testing.

Docker has been started. The required v5.5.1 image was absent and its download
is in progress. No paired build has been started, and no Docker image ID has
been invented.
Before flashing, verify a current full-device recovery snapshot and the live
partition table, present actual candidate artifacts/provenance and obtain the
user's explicit approval. An earlier recovery image alone does not prove the
current filesystem has been preserved.

## Phase 1 measured results

Deployment verified on-device against all 20 host hashes (17 modules, main.py,
loader and manifest). The device reports FW_STORAGE filesystem. No base firmware,
settings, calibration or initialization policy was changed.

The complete physical cold boot reproduces the measurements below from the
first captured hardware-reset run, including every listed GC/native value.
Native free is summed across the six non-overlapping HEAP_DATA regions; largest
is the maximum available block, not the size of an allocation actually made.

| Checkpoint | GC total | GC used | GC free | Max new split | Native free | Largest native block | Stack used / limit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Before captive_portal import (after GC) | 56,000 | 46,288 | 9,712 | 102,400 | 102,540 | 102,400 | 1,456 / 15,360 |
| After captive_portal import | 112,000 | 64,368 | 47,632 | 43,008 | 43,144 | 43,008 | 1,456 / 15,360 |
| Before Wi-Fi startup | 112,000 | 110,096 | 1,904 | 43,008 | 43,144 | 43,008 | 1,760 / 15,360 |
| Before GPIO import (after GC) | 112,000 | 109,232 | 2,768 | 43,008 | 43,144 | 43,008 | 3,488 / 15,360 |
| After GPIO import execution | 153,920 | 114,496 | 39,424 | 76 | 132 | 76 | 3,488 / 15,360 |
| After control modules (after GC) | 153,920 | 113,568 | 40,352 | 76 | 132 | 76 | 3,520 / 15,360 |
| Before ambient ADC creation | 153,920 | 114,016 | 39,904 | 76 | 132 | 76 | 3,760 / 15,360 |

The final expansion occurs **inside `_fw_gpio` import execution**, not in its
surrounding explicit collections: GC capacity increases by 41,920 bytes and
native free decreases by 43,012 bytes. There is also an earlier 56,000-byte GC
capacity increase during captive_portal import. Existing Python free memory
therefore cannot be treated as native headroom.

GPIO import completes; AmbientLightController starts and enters ADC construction.
No after-ADC marker or lighting-PWM-creation marker is reached. Wi-Fi startup
has not returned, no portal-ready checkpoint is reached, and runtime tasks have
not been created. A full initialized-startup measurement does not exist.

The confirmed Ctrl-D run reproduces the same GC totals/used/free and exact
43,012-byte native loss during GPIO import. Its native free is 43,136 bytes before
and 124 bytes after, with the same largest blocks (43,008 and 76). It again aborts
inside ambient ADC creation. Subsequent automatic CPU resets reproduce the
failure; these are not counted as additional physical cold boots.

## Matched native crash decoding

Downloaded the official ESP32_GENERIC v1.28.0 ELF/map archive from:
https://micropython.org/resources/firmware/ESP32_GENERIC-20260406-v1.28.0.meta.zip

ELF SHA-256:
`cc85d995e8b415bd66c20a7dea84b5117a08c05a3c0f9ea791bc29a9251487a1`,
matching the panic's `cc85d995e` prefix. The full 32-byte ELF hash is also
present at flash offset `0x100b0` in the recovery image whose application
partition matches the running device, confirming the full hash association. All non-ROM frames were resolved using
ELF symbols and DWARF line ranges; complete PC:SP order is retained in
backtrace-symbols.json. The matching debug files remain under
`.firmware-build/releases/`.

The relevant chain is:

```text
mp_machine_adc_make_new          machine_adc.c:222
madc_init_helper                machine_adc.c:191
adc_oneshot_new_unit             adc_oneshot.c:95
esp_log                         log.c:43
vprintf -> console/UART write
_lock_acquire_recursive         locks.c:174
lock_acquire_generic            locks.c:77 (inlined lock_init_generic)
abort
```

`adc_oneshot.c:94` allocates the ADC unit structure; line 95 logs
`no mem for unit` when that allocation fails. While trying to print this error,
UART lazily initializes a recursive mutex; `locks.c:75–77` calls
xQueueCreateMutex and aborts if it returns NULL. Thus the ADC allocation fails
first, and failure to allocate the logging mutex produces the fatal abort.
The native-starvation chain is now supported by measured expansion and matched
source-level crash evidence, not solely by RAM arithmetic.

DWARF paths contain an old directory name `esp-idf-v5.0`; that path name is not
the SDK version. The device diagnostic reports IDF v5.5.1, and the corresponding
local v5.5.1 source statements were checked.

The repeated PCs resolve to mp_call_function_n_kw, mp_call_method_n_kw,
mp_execute_bytecode and fun_bc_call along the nested startup callback chain.
They do not establish recursive initialization. Stack samples show substantial
headroom, and the decoded abort is the OOM branch, not a stack-canary/overflow
failure. Checkpoint samples still do not measure every transient stack peak.

## Requested comparison

Filesystem numbers below refer explicitly to the **last completed control-module
checkpoint**, not a successful end of startup. Frozen values remain unmeasured.
No estimates are substituted for device measurements.

| Measurement | Filesystem | Frozen |
| --- | --- | --- |
| GC heap total after startup | Startup incomplete; 153,920 B after controls | Not tested |
| GC heap used | 113,568 B after controls | Not tested |
| GC heap free | 40,352 B after controls | Not tested |
| Native free memory | 132 B after controls (124 B after confirmed soft reset) | Not tested |
| Largest native allocation | Largest free block: 76 B | Not tested |
| Native memory after Wi-Fi | Not reached; 43,144 B before Wi-Fi startup | Not tested |
| Native memory after portal startup | Not reached | Not tested |
| Startup crash | Yes, ADC OOM followed by logging mutex OOM | Not tested |

## Phase 3 status

Deferred until an approved frozen image has been installed and startup improves.
STA, portal, scans, NTP, lighting, pump, sensors, navigation and reconnection have
not passed device acceptance. The existing fan implementation drives a digital
pin on/off; fan PWM is not implemented in gpio.py, so it cannot be claimed as a
validated PWM function without a separately scoped behavior change.

Raw logs and per-checkpoint JSON retain all native regions and minimum-free
watermarks, including aborted/incomplete startup runs. The file named
filesystem-soft-reset.log is an unsuccessful soft-reset attempt (no soft marker);
use filesystem-soft-reset-confirmed.log for the confirmed Ctrl-D comparison.

The device was left at the REPL after captures, with outputs 13/18/23/5/25/15
set low to stop the crash loop and leave pump/lighting/EC/fan/operation LED off.
No startup source behavior was changed for this idle state.
