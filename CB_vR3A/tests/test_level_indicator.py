import utime
from app.level_indicator import LevelIndicator

leds = LevelIndicator(
    data_pin=4,
    clock_pin=14,
    latch_pin=22
)

print("Level indicator test started")
print("DATA=IO4, CLOCK=IO14, LATCH=IO22")
print("LEDs are assumed active-low")

while True:
    print("Clear / all off")
    leds.clear()
    utime.sleep(2)

    print("All on")
    leds.all_on()
    utime.sleep(2)

    print("Single LED test")
    for i in range(8):
        print("LED", i + 1)
        leds.single_led(i)
        utime.sleep(0.5)

    print("Bar graph")
    for i in range(9):
        print("Level", i)
        leds.bar(i)
        utime.sleep(0.4)