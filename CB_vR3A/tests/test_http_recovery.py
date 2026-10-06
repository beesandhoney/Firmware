import ast
import errno
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


if __name__ == '__main__':
    unittest.main()
