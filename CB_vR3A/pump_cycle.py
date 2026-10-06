import utime


class PumpCycle:
    ON_MS = 120000
    OFF_MS = 1200000

    def __init__(self, pin):
        self.pin = pin
        self.is_on = True
        self.pin.value(self.is_on)
        self.deadline = utime.ticks_add(utime.ticks_ms(), self.ON_MS)

    def tick(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self.deadline) < 0:
            return
        self.is_on = not self.is_on
        self.pin.value(self.is_on)
        duration = self.ON_MS if self.is_on else self.OFF_MS
        # Give each phase its full duration even if servicing was delayed.
        self.deadline = utime.ticks_add(now, duration)
        print("pump is on" if self.is_on else "pump is off")
