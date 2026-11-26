# Portal memory diagnosis — 2026-11-07

The portal-routing-3 iPhone association log shows native heap starvation before
any HTTP request. Station count changes 0 → 1 → 0 repeatedly. Native free memory
drops from 38,416 bytes after HTTP module loading to 1,548 bytes at the first
station report and about 400 bytes during association.

## Correct interpretation of the counters

On ESP32 MicroPython v1.28, `gc.mem_free()` includes existing GC free space **plus**
the native heap space available for an automatic new GC split. It is not an
independent Python-only memory reserve. The earlier `python_free` log label and
interpretation were misleading.

| Bytes | After HTTP module load | First station report |
|---|---:|---:|
| Python allocated | 109,728 | 113,136 |
| Reported `gc.mem_free()` | 39,136 | 36,112 |
| Largest native block | 36,864 | 1,280 |
| Existing GC free (reported minus largest native block) | 2,272 | 34,832 |
| Existing GC capacity (allocated plus existing free) | 112,000 | 147,968 |
| Native free | 38,416 | 1,548 |

This calculation assumes the reported HEAP_DATA largest block is the same block
available through MALLOC_CAP_DEFAULT on this generic ESP32; the observed region
layout and upstream implementation support that interpretation. Measurements are
sampled separately, and logging itself allocates.

Python capacity increased by 35,968 bytes while native free decreased by 36,868
bytes: the previous 36,864-byte block plus allocator overhead. The source growth
policy and this arithmetic strongly identify automatic GC expansion taking the
last large native block. Only about 3.4 KB more live Python allocation was needed
to cross the original capacity. `micropython.mem_info()` prints actual GC free,
total and max-new-split separately and is the direct confirmation instrument.

## Why earlier mitigations are insufficient

The compiled loader, `gc.collect()` and `gc.threshold(4096)` are still present.
Filesystem `.mpy` files avoid source compilation peaks, but their bytecode still
uses RAM after loading. Application growth has left too little managed-heap
space for HTTP server objects and runtime allocations. The allocator then grows
the managed heap using memory needed by the native Wi-Fi stack.

Deferring NTP/asyncio did not prevent this expansion in the device log. The
missing-icon redirect correction removes unnecessary HTTP traffic but cannot
fix a failure before HTTP. There is no evidence that retries, browser settings,
or the socket backlog caused this particular 36 KB allocation.

## Next implementation and acceptance

Build a custom MicroPython v1.28.0 ESP32_GENERIC image with application bytecode
frozen into flash. Preserve the existing module-alias/loading contract and ensure
old filesystem `.mpy` copies do not override the frozen modules. Keep settings,
credentials, calibration and mutable web assets in the device filesystem.
The upstream recommended build SDK for this version is ESP-IDF v5.5.1.

This is a base-firmware build, not another ordinary `.mpy` update. No such image
has been built or flashed yet. Before any flash operation, verify the device
flash size/partition map and preserve its settings/calibration and recovery
image. Prefer application-partition-only updating when the verified layout
allows it; do not blindly erase or overwrite the filesystem.

A native-memory reserve in a custom allocator can be considered separately, but
it must be paired with reduced Python residency: a reserve alone could merely
turn Wi-Fi starvation into Python MemoryError. `gc.threshold()` does not impose
a reserve or cap, and `gc.disable()` does not prevent split-heap expansion.

Accept only after comparing actual GC capacity/free and native free/largest at
boot, AP startup, association, scanning, repeated navigation and reconnects.
Require successful association/HTTP, working lighting/pump controls, no memory
errors and sufficient measured margin under these workloads. Host tests alone
cannot establish those properties on the ESP32.

## Primary references

- [ESP32 heap and gc.mem_free semantics](https://docs.micropython.org/en/v1.28.0/library/esp32.html#esp32.idf_heap_info)
- [GC growth policy](https://github.com/micropython/micropython/blob/v1.28.0/py/gc.c)
- [ESP32 split-size implementation](https://github.com/micropython/micropython/blob/v1.28.0/ports/esp32/gccollect.c)
- [Frozen bytecode and flash residency](https://docs.micropython.org/en/v1.28.0/reference/manifest.html)
- [ESP32 build instructions](https://github.com/micropython/micropython/blob/v1.28.0/ports/esp32/README.md)
