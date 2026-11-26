"""Repeatable serial memory samples for filesystem/frozen build comparisons.

Preload this module at the same point in both builds. No collection is forced:
the sample observes the running allocation policy. Printing and native heap-info
construction themselves have a cost; the GC and native readings are sequential.
"""
import esp32
import micropython
import utime


def report(context="portal", operation=None):
    if operation is None:
        print("MEM_BEGIN", utime.ticks_ms(), context)
    else:
        print("MEM_BEGIN", utime.ticks_ms(), context, operation)
    try:
        # gc.mem_free() includes a prospective native split on ESP32. mem_info
        # separates existing GC capacity/free from that prospective allocation.
        micropython.mem_info()
        # mem_info prints actual GC total/used/free, max-new-split and stack
        # used/limit without building a Python dictionary or parsing its output.
        # HEAP_DATA is MALLOC_CAP_8BIT, not MALLOC_CAP_DEFAULT (GC growth).
        heaps = esp32.idf_heap_info(esp32.HEAP_DATA)
        for index in range(len(heaps)):
            row = heaps[index]
            print("MEM_NATIVE", index, row[0], row[1], row[2], row[3])
    except Exception as error:
        print("MEM_ERROR", error)
    print("MEM_END")


def tasks():
    """Optional, heavier FreeRTOS stack-watermark sample; call from the REPL.

    Not run at every checkpoint: idf_task_info allocates a task array and Python result objects.
    """
    info = getattr(esp32, "idf_task_info", None)
    if info is None:
        print("MEM_TASK unavailable")
        return
    try:
        runtime, rows = info()
        for row in rows:
            print("MEM_TASK", row[0], row[1], "stack_watermark", row[5])
    except Exception as error:
        print("MEM_TASK_ERROR", error)
