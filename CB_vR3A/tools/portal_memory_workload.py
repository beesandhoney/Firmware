#!/usr/bin/env python3
"""Read-only external HTTP workload for comparing ESP32 portal builds.

Run this on a computer joined to the device's setup AP. It does not access serial,
change credentials/settings, follow redirects, or print response bodies.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import socket
import threading
import time
from typing import NamedTuple
from urllib.parse import urlsplit

WORKLOAD_VERSION = 1
MAX_BODY_BYTES = 256 * 1024


class Step(NamedTuple):
    name: str
    path: str
    kind: str
    status: int = 200


# Stages retain navigation order. Only status/icon stages can overlap; scanning
# stays isolated, matching wifi.js's scan-then-poll behavior.
STAGES = (
    (Step('garden', '/', 'html'),),
    (Step('garden_status', '/api/status', 'status'),
     Step('apple_icon', '/apple-touch-icon.png', 'empty', 404),
     Step('apple_precomposed_icon', '/apple-touch-icon-precomposed.png', 'empty', 404),
     Step('favicon', '/favicon.ico', 'empty', 404)),
    (Step('settings_menu', '/settings', 'html'),),
    (Step('menu_status', '/api/portal', 'portal'),),
    (Step('grow_settings', '/grow-settings', 'html'),),
    (Step('settings_read', '/api/settings', 'settings'),),
    (Step('wifi_page', '/wifi', 'html'),),
    (Step('wifi_scan', '/api/networks', 'networks'),),
    (Step('wifi_status', '/api/portal', 'portal'),),
    (Step('return_garden', '/garden', 'html'),),
    (Step('return_status', '/api/status', 'status'),
     Step('return_favicon', '/favicon.ico', 'empty', 404)),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def device_address(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('Use an HTTP origin only, such as http://192.168.4.1/')
    # Exclude DNS/mDNS behavior and its separately controlled timeouts.
    ipaddress.ip_address(parsed.hostname)
    port = parsed.port if parsed.port is not None else 80
    if port == 0:
        raise ValueError('Use a nonzero TCP port')
    return parsed.hostname, port


def validate_body(step, content_type, body):
    """Return a fixed error label; never include response content in output."""
    if step.kind == 'empty':
        return None if not body else 'unexpected_body'
    if step.kind == 'html':
        if content_type != 'text/html':
            return 'unexpected_content_type'
        return None if b'<html' in body.lower() and b'</html>' in body.lower() else 'invalid_html'
    if content_type != 'application/json':
        return 'unexpected_content_type'
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return 'invalid_json'
    if not isinstance(value, dict):
        return 'invalid_json_shape'
    valid = False
    if step.kind == 'status':
        valid = all(isinstance(value.get(key), dict)
                    for key in ('clock', 'lighting', 'water', 'nutrients', 'temperature'))
    elif step.kind == 'portal':
        valid = isinstance(value.get('connected'), bool) and isinstance(value.get('connecting'), bool)
    elif step.kind == 'settings':
        valid = isinstance(value.get('timezone'), str) and isinstance(value.get('lighting'), dict)
    elif step.kind == 'networks':
        names = value.get('networks')
        valid = isinstance(names, list) and all(isinstance(name, str) for name in names)
    return None if valid else 'invalid_json_shape'


def request_once(address, step, timeout=8.0):
    """One fresh connection, with a deadline spanning headers and entire body."""
    start = time.monotonic()
    deadline = start + timeout
    result = dict(type='request', timestamp_utc=utc_now(), step=step.name,
                  path=step.path, expected_status=step.status, status=None,
                  content_length=None, bytes_received=0, ok=False, error=None)
    conn = http.client.HTTPConnection(address[0], address[1], timeout=timeout)
    expired = threading.Event()
    timer = None
    response = None
    try:
        # Numeric destination means connect() itself is bounded by timeout.
        conn.connect()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        active_socket = conn.sock
        active_socket.settimeout(remaining)

        def abort():
            expired.set()
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active_socket.close()

        # A socket timeout alone does not bound a peer trickling header/body
        # bytes. Interrupt its socket at the overall request deadline as well.
        timer = threading.Timer(remaining, abort)
        timer.daemon = True
        timer.start()
        conn.request('GET', step.path, headers={
            'Connection': 'close', 'Cache-Control': 'no-cache',
            'Accept-Encoding': 'identity', 'User-Agent': 'GrowMemoryWorkload/1',
        })
        response = conn.getresponse()
        result['status'] = response.status
        if response.status != step.status:
            result['error'] = 'unexpected_status'
        elif response.getheader('Transfer-Encoding'):
            result['error'] = 'unexpected_transfer_encoding'
        else:
            lengths = response.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].strip().isdigit():
                result['error'] = 'missing_or_invalid_content_length'
            else:
                expected = int(lengths[0])
                result['content_length'] = expected
                if expected > MAX_BODY_BYTES:
                    result['error'] = 'body_too_large'
                else:
                    body = bytearray()
                    while len(body) < expected:
                        chunk = response.read1(min(8192, expected - len(body)))
                        if not chunk:
                            break
                        body.extend(chunk)
                        result['bytes_received'] = len(body)
                    if len(body) != expected:
                        result['error'] = 'incomplete_body'
                    else:
                        content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
                        result['error'] = validate_body(step, content_type, body)
                        if step.kind == 'html' and result['error'] is None:
                            result['body_sha256'] = hashlib.sha256(body).hexdigest()
    except (TimeoutError, socket.timeout):
        result['error'] = 'timeout'
    except ConnectionRefusedError:
        result['error'] = 'connection_refused'
    except http.client.HTTPException:
        result['error'] = 'invalid_http'
    except OSError as error:
        result['error'] = 'transport_error'
        result['errno'] = error.errno
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        conn.close()
    if expired.is_set() or time.monotonic() >= deadline:
        result['error'] = 'timeout'
    result['elapsed_ms'] = round((time.monotonic() - start) * 1000, 2)
    result['ok'] = result['error'] is None
    return result


def run_workload(url, build_label, cycles, timeout, concurrency, delay, emit):
    address = device_address(url)
    emit(dict(type='metadata', timestamp_utc=utc_now(), workload_version=WORKLOAD_VERSION,
              build_label=build_label, device_url=url, cycles=cycles,
              timeout_seconds=timeout, concurrency=concurrency, stage_delay_seconds=delay,
              method='GET', follow_redirects=False, max_body_bytes=MAX_BODY_BYTES,
              label_is_operator_supplied=True,
              stages=[[step._asdict() for step in stage] for stage in STAGES]))
    total = failed = 0
    elapsed_max = 0.0
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for cycle in range(1, cycles + 1):
            for stage_index, stage in enumerate(STAGES, 1):
                pending = {pool.submit(request_once, address, step, timeout): index
                           for index, step in enumerate(stage, 1)}
                for future in concurrent.futures.as_completed(pending):
                    record = future.result()
                    record.update(cycle=cycle, stage=stage_index, stage_step=pending[future])
                    emit(record)
                    total += 1
                    failed += int(not record['ok'])
                    elapsed_max = max(elapsed_max, record['elapsed_ms'])
                if delay:
                    time.sleep(delay)
    summary = dict(type='summary', timestamp_utc=utc_now(), requests=total,
                   passed=total-failed, failed=failed, ok=failed == 0,
                   elapsed_ms=round((time.monotonic()-started)*1000, 2),
                   slowest_request_ms=elapsed_max)
    emit(summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://192.168.4.1/', help='Numeric HTTP origin; no DNS or redirects.')
    parser.add_argument('--build-label', required=True, help='Operator label; confirm the actual build in serial first.')
    parser.add_argument('--cycles', type=int, default=3, help='Fixed navigation cycles (1–100; default 3).')
    parser.add_argument('--timeout', type=float, default=8.0, help='Whole-request deadline, seconds (0–60; default 8).')
    parser.add_argument('--concurrency', type=int, choices=(1, 2, 3), default=1,
                        help='Concurrent requests within status/icon stages only (default 1).')
    parser.add_argument('--stage-delay', type=float, default=0.25,
                        help='Pause after each stage, seconds (0–10; default 0.25).')
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 100 or not 0 < args.timeout <= 60 or not 0 <= args.stage_delay <= 10:
        parser.error('cycles, timeout, or stage delay is outside its documented bounds')
    try:
        device_address(args.url)
    except ValueError:
        parser.error('url must be a numeric HTTP origin without credentials, path, query, or fragment')
    def emit(record):
        print(json.dumps(record, sort_keys=True), flush=True)
    result = run_workload(args.url, args.build_label, args.cycles, args.timeout,
                          args.concurrency, args.stage_delay, emit)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
