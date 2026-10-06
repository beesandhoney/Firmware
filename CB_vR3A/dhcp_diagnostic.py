"""Standalone DHCP check. Run after interrupting main.py at the serial REPL.

Creates GrowDHCPTest using the ESP32's default AP IPv4/DHCP configuration.
Does not load settings, sensors, HTTP or DNS. Power-cycle to return to main.py.
"""
import network
import time
import sys


def run():
    print('MicroPython:', sys.implementation)
    sta = network.WLAN(network.STA_IF)
    ap = network.WLAN(network.AP_IF)
    sta.active(False)
    ap.active(False)
    time.sleep_ms(500)
    # Configure identity before activation. Deliberately do not call ifconfig
    # with arguments: this tests the port's default DHCP startup path.
    ap.config(essid='GrowDHCPTest', authmode=network.AUTH_OPEN,
              channel=1, max_clients=4)
    ap.active(True)
    time.sleep_ms(1000)
    print('AP network configuration:', ap.ifconfig())
    print('Join GrowDHCPTest with Configure IP set to Automatic.')
    print('Check the phone IP and Router fields. No web page is served here.')
    previous = None
    try:
        while True:
            count = len(ap.status('stations'))
            if count != previous:
                print('Associated clients:', count, '(not proof of a DHCP lease)')
                previous = count
            time.sleep_ms(1000)
    except KeyboardInterrupt:
        print('Diagnostic stopped. Power-cycle the device to restore normal operation.')
    finally:
        ap.active(False)


if __name__ == '__main__':
    run()
