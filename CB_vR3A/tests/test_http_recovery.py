import ast
import errno
import io
from pathlib import Path
import types
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_method(filename, class_name, method_name, namespace):
    # Isolate networking control flow from MicroPython hardware imports.
    tree = ast.parse((ROOT / filename).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[method], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[method_name]


class HTTPRecoveryTest(unittest.TestCase):
    def test_runtime_idle_poll_does_not_sleep(self):
        sleep = Mock()
        method = load_method('captive_http.py', 'HTTPServer', 'serve_once',
                             {'utime': types.SimpleNamespace(sleep_ms=sleep)})
        sock = Mock()
        sock.accept.side_effect = OSError(errno.EAGAIN, 'idle')
        server = types.SimpleNamespace(sock=sock, is_transient_socket_error=lambda e: e.errno == errno.EAGAIN)
        method(server, 0)
        sleep.assert_not_called()
        method(server, 250)
        sleep.assert_called_once_with(250)

    def test_accept_idle_is_harmless_but_broken_listener_propagates(self):
        method = load_method('captive_http.py', 'HTTPServer', 'serve_once',
                             {'utime': types.SimpleNamespace(sleep_ms=Mock())})
        server = types.SimpleNamespace(sock=Mock(),
            is_transient_socket_error=lambda e: e.errno == errno.EAGAIN)
        server.sock.accept.side_effect = OSError(errno.EAGAIN, 'idle')
        self.assertTrue(method(server, 0))
        server.sock.accept.side_effect = OSError(errno.EBADF, 'closed listener')
        with self.assertRaises(OSError):
            method(server, 0)

    def test_partial_sends_and_backpressure_preserve_exact_response_bytes(self):
        sleep = Mock()
        send_all = load_method('captive_http.py', 'HTTPServer', '_write_all_blocking',
            dict(uerrno=errno, utime=types.SimpleNamespace(sleep_ms=sleep), RESPONSE_WRITE_RETRIES=300, RESPONSE_RETRY_DELAY_MS=10))
        output = bytearray()
        attempts = [0]
        def send(data):
            attempts[0] += 1
            if attempts[0] <= 10:
                raise OSError(errno.EAGAIN, 'queue full')
            count = min(7, len(data))
            output.extend(data[:count])
            return count
        sock = types.SimpleNamespace(send=send)
        server = types.SimpleNamespace(_response_bytes=0, _response_stalled=False, _log_send_event=Mock())
        data = bytes(range(256)) * 3
        self.assertTrue(send_all(server, sock, data))
        self.assertEqual(bytes(output), data)
        self.assertEqual(sleep.call_count, 10)

    def test_send_stall_is_bounded(self):
        sleep = Mock()
        send_all = load_method('captive_http.py', 'HTTPServer', '_write_all_blocking',
            dict(uerrno=errno, utime=types.SimpleNamespace(sleep_ms=sleep), RESPONSE_WRITE_RETRIES=3, RESPONSE_RETRY_DELAY_MS=10))
        sock = types.SimpleNamespace(send=Mock(return_value=None))
        server = types.SimpleNamespace(_response_bytes=0, _response_stalled=False, _log_send_event=Mock())
        self.assertFalse(send_all(server, sock, b'body'))
        self.assertEqual(sock.send.call_count, 3)

    def test_disconnected_clients_are_not_retried(self):
        for code in (errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE):
            with self.subTest(code=code):
                sleep = Mock()
                send_all = load_method('captive_http.py', 'HTTPServer', '_write_all_blocking',
                    dict(uerrno=errno, utime=types.SimpleNamespace(sleep_ms=sleep),
                         RESPONSE_WRITE_RETRIES=300, RESPONSE_RETRY_DELAY_MS=10))
                sock = types.SimpleNamespace(send=Mock(side_effect=[2, OSError(code, 'closed')]))
                server = types.SimpleNamespace(_response_bytes=100, _response_stalled=False,
                                               _log_send_event=Mock())
                self.assertFalse(send_all(server, sock, b'body'))
                self.assertEqual(server._response_bytes, 102)
                self.assertEqual(sock.send.call_count, 2)
                sleep.assert_not_called()
                server._log_send_event.assert_called_once_with('socket-error', code)

    def test_backpressure_logs_once_and_retains_retry_budget(self):
        sleep = Mock()
        send_all = load_method('captive_http.py', 'HTTPServer', '_write_all_blocking',
            dict(uerrno=errno, utime=types.SimpleNamespace(sleep_ms=sleep),
                 RESPONSE_WRITE_RETRIES=300, RESPONSE_RETRY_DELAY_MS=10))
        sock = types.SimpleNamespace(send=Mock(side_effect=OSError(errno.EAGAIN, 'full')))
        server = types.SimpleNamespace(_response_bytes=536, _response_stalled=False,
                                       _log_send_event=Mock())
        self.assertFalse(send_all(server, sock, b'body'))
        self.assertEqual(sock.send.call_count, 300)
        self.assertEqual(sleep.call_count, 299)
        self.assertEqual(server._response_bytes, 536)
        self.assertEqual(server._log_send_event.call_args_list,
                         [unittest.mock.call('backpressure', errno.EAGAIN),
                          unittest.mock.call('retry-limit', errno.EAGAIN)])

    def test_diagnostics_report_total_bytes_and_elapsed_time(self):
        log = load_method('captive_http.py', 'HTTPServer', '_log_send_event',
            dict(utime=types.SimpleNamespace(ticks_ms=lambda: 4500, ticks_diff=lambda a,b: a-b)))
        server = types.SimpleNamespace(_response_path=b'/wifi', _response_bytes=1234,
                                       _response_started_ms=1000)
        with unittest.mock.patch('builtins.print') as output:
            log(server, 'socket-error', errno.ECONNRESET)
        message = output.call_args[0][0]
        for field in ("path=b'/wifi'", 'sent=1234', 'elapsed_ms=3500', 'errno={}'.format(errno.ECONNRESET)):
            self.assertIn(field, message)

    def test_response_stream_counts_headers_and_all_chunks_and_closes(self):
        clock = types.SimpleNamespace(ticks_ms=lambda: 100, ticks_diff=lambda a,b: a-b)
        ns = dict(uerrno=errno, utime=clock, RESPONSE_WRITE_RETRIES=300,
                  RESPONSE_RETRY_DELAY_MS=10, RESPONSE_CHUNK_SIZE=3)
        server = types.SimpleNamespace(_response_path=b'/wifi')
        for name in ('_write_all_blocking', '_log_send_event', '_ensure_connection_close'):
            method = load_method('captive_http.py', 'HTTPServer', name, ns)
            setattr(server, name, types.MethodType(method, server))
        write = load_method('captive_http.py', 'HTTPServer', 'write_blocking', ns)
        body = io.BytesIO(b'abcdefg')
        output = bytearray()
        def send(data):
            output.extend(data[:2])
            return min(2, len(data))
        sock = types.SimpleNamespace(send=send, setblocking=Mock())
        with unittest.mock.patch('builtins.print') as log:
            self.assertTrue(write(server, sock, body, b'HTTP/1.1 200 OK\r\n'))
        self.assertEqual(bytes(output), b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nabcdefg')
        self.assertEqual(server._response_bytes, len(output))
        self.assertTrue(body.closed)
        self.assertIn('event=complete', log.call_args[0][0])

    def test_first_stall_reports_memory_and_diagnostic_failure_is_harmless(self):
        log = load_method('captive_http.py', 'HTTPServer', '_log_send_event',
            dict(utime=types.SimpleNamespace(ticks_ms=lambda: 100, ticks_diff=lambda a,b: a-b)))
        server = types.SimpleNamespace(_response_path=b'/', _response_bytes=0,
            _response_started_ms=0, memory_reporter=Mock(side_effect=MemoryError()))
        with unittest.mock.patch('builtins.print'):
            log(server, 'backpressure', errno.EAGAIN)
            log(server, 'complete', None)
        server.memory_reporter.assert_called_once_with('HTTP first send stall')

    def test_failed_listener_restarts_with_backoff_and_preserves_mode(self):
        now = [0]
        method = load_method('captive_portal.py', 'CaptivePortal', '_poll_once',
            {'time': types.SimpleNamespace(ticks_ms=lambda: now[0], ticks_diff=lambda a, b: a-b)})
        listener = Mock()
        listener.serve_once.side_effect = OSError(errno.EBADF, 'closed')
        server = types.SimpleNamespace(http_server=listener, _poll_error_count=0,
            _last_http_restart_ms=None, HTTP_RESTART_INTERVAL_MS=2000, portal_mode='settings')
        server._stop_http_server = lambda: setattr(server, 'http_server', None)
        server._start_http_server = Mock(return_value=False)
        self.assertFalse(method(server, 0, 'settings'))
        self.assertIsNone(server.http_server)
        self.assertFalse(method(server, 0, 'settings'))
        server._start_http_server.assert_called_once_with('settings')
        now[0] = 1000
        self.assertFalse(method(server, 0, 'settings'))
        self.assertEqual(server._start_http_server.call_count, 1)
        recovered = Mock()
        def restart(mode):
            server.http_server = recovered
            return True
        server._start_http_server.side_effect = restart
        now[0] = 2000
        self.assertTrue(method(server, 0, 'settings'))
        recovered.serve_once.assert_called_once_with(0)

    def test_close_failure_is_reported_and_gc_still_runs(self):
        collect = Mock()
        serve = load_method('captive_http.py', 'HTTPServer', 'serve_once',
            dict(gc=types.SimpleNamespace(collect=collect)))
        client = Mock()
        client.close.side_effect = OSError(errno.EAGAIN, 'close would block')
        server = types.SimpleNamespace(sock=Mock(), is_transient_socket_error=lambda e: False)
        server.sock.accept.return_value = (client, None)
        def handle(sock):
            server._response_path = b'/settings'
            return False
        server.handle_blocking = handle
        with unittest.mock.patch('builtins.print') as output:
            self.assertTrue(serve(server, 0))
        self.assertIn("HTTP close failed: path=b'/settings'", output.call_args[0][0])
        self.assertIn('errno={}'.format(errno.EAGAIN), output.call_args[0][0])
        collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
