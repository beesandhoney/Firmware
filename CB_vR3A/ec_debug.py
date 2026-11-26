"""Standalone EC monitor on GPIO34 for a board with no power-control circuit."""
from machine import ADC, Pin
import utime


def main(interval_ms=500):
    try:
        adc = ADC(Pin(34))
        adc.atten(ADC.ATTN_11DB)
        utime.sleep_ms(200)
        print("EC only: GPIO34 ADC, externally powered; Ctrl-C to stop")
        deadline = utime.ticks_ms()
        while True:
            raw = adc.read()
            millivolts = adc.read_uv() // 1000
            print("EC raw={} voltage={} mV{}".format(
                raw, millivolts, " ADC AT RAIL" if raw in (0, 4095) else ""))
            deadline = utime.ticks_add(deadline, interval_ms)
            remaining = utime.ticks_diff(deadline, utime.ticks_ms())
            if remaining > 0:
                utime.sleep_ms(remaining)
            else:
                deadline = utime.ticks_ms()
    except KeyboardInterrupt:
        print("EC debug stopped")


if __name__ == "__main__":
    main()
