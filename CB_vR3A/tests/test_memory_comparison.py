"""Serial capture comparison uses real GC capacity, including incomplete runs."""
import ast
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compare_memory', ROOT / 'tools' / 'compare_memory.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def sample(context='portal', total=112000, used=109728, free=2272,
           split=36864, native_free=38416, largest=36864, end=True):
    return ('MEM_BEGIN 1200 {}\nGC: total: {}, used: {}, free: {}, max new split: {}\n'
            ' No. of 1-blocks: 20, 2-blocks: 30, max blk sz: 500, max free sz: 40\n'
            'MEM_NATIVE 0 240 4 0 4\nMEM_NATIVE 1 113840 {} {} 1508\n{}').format(
                context, total, used, free, split, native_free, largest,
                'MEM_END\n' if end else '')


def capture(storage='filesystem', body=None, build='test-1'):
    return ('rst:0xc (SW_CPU_RESET),boot:0x12\nFirmware build: {}\nFW_STORAGE {}\n'
            'mem_free after HTTP load: 39136\n{}').format(
                build, storage, sample() if body is None else body)


class MemoryComparisonTest(unittest.TestCase):
    def test_existing_gc_free_never_includes_max_new_split(self):
        log = comparison.parse_log(capture(body=sample(total=147968, used=113136,
            free=34832, split=1280, native_free=1548, largest=1280)))
        stats = comparison.summarize(log)[0]['phases']['http.after_load']
        self.assertEqual(stats['gc_capacity_max'], 147968)
        self.assertEqual(stats['gc_free_min'], 34832)
        self.assertEqual(stats['native_free_min'], 1552)
        self.assertEqual(stats['native_largest_min'], 1280)
        before = comparison.summarize(comparison.parse_log(capture()))[0]['phases']['http.after_load']
        self.assertEqual(before['gc_free_min'], 2272)  # not advertised 39,136

    def test_complete_comparison_preserves_raw_values_and_deltas(self):
        baseline = comparison.parse_log(capture(), 'current.log')
        frozen = comparison.parse_log(capture('frozen', sample(used=70000, free=42000)), 'frozen.log')
        report = comparison.compare(baseline, frozen)
        delta = report['pairs'][0]['phase_deltas']['http.after_load']
        self.assertEqual(delta['gc_free_min'], 39728)
        self.assertEqual(delta['gc_used_max'], -39728)
        self.assertFalse(report['warnings'])
        self.assertEqual(report['baseline']['source'], 'current.log')
        rendered = comparison.render(report)
        self.assertIn('2272 -> 42000 (+39728)', rendered)

    def test_truncated_and_missing_measurements_are_retained_but_excluded(self):
        for body in (sample(end=False), 'MEM_BEGIN 1 portal\nMEM_END\n',
                     'MEM_BEGIN 1 portal\nGC: total: broken\nMEM_END\n'):
            with self.subTest(body=body):
                log = comparison.parse_log(capture(body=body))
                self.assertFalse(log['runs'][0]['samples'][0]['complete'])
                self.assertTrue(log['warnings'])
                summary = comparison.summarize(log)[0]
                self.assertFalse(summary['phases'])
                self.assertEqual(summary['incomplete_samples'], 1)

    def test_reboot_during_sample_keeps_runs_separate(self):
        log = comparison.parse_log(capture(body=sample(end=False)) +
            'ets Jul 29 2019 12:21:46\n' + capture('frozen', build='test-2'))
        self.assertEqual(len(log['runs']), 2)
        self.assertEqual([r['index'] for r in log['runs']], [1, 2])
        self.assertFalse(log['runs'][0]['samples'][0]['complete'])
        self.assertTrue(log['runs'][1]['samples'][0]['complete'])
        self.assertEqual(log['runs'][1]['build'], 'test-2')
        self.assertEqual(log['runs'][1]['storage'], 'frozen')

    def test_repeated_runs_are_not_pooled_or_silently_dropped(self):
        a = comparison.parse_log(capture() + capture())
        b = comparison.parse_log(capture('frozen'))
        report = comparison.compare(a, b)
        self.assertEqual(len(report['pairs']), 2)
        self.assertIsNone(report['pairs'][1]['candidate'])
        self.assertEqual(report['pairs'][0]['baseline']['phases']['http.after_load']['samples'], 1)
        self.assertIn('run counts differ', '\n'.join(report['warnings']))

    def test_phase_markers_and_failures(self):
        body = (sample('before control modules') + sample('after control modules') +
                'AP station count: 0\n' + sample() + 'AP station count: 1\n' + sample() +
                'HTTP TX: path=b"/" event=retry-limit errno=11 sent=0 elapsed_ms=3200\n'
                'E (16889) wifi:setup rate: out of memory!\n' + sample('HTTP first send stall'))
        run = comparison.summarize(comparison.parse_log(capture(body=body)))[0]
        self.assertEqual(set(run['phases']), {'controls.before_load', 'controls.after_load',
            'ap.no_client', 'ap.client_connected', 'http.send_stall'})
        self.assertEqual(run['driver_oom_count'], 1)
        self.assertEqual(run['http_failure_count'], 1)

    def test_missing_phase_and_storage_are_explicit(self):
        a = comparison.parse_log(capture())
        b = comparison.parse_log(capture('unknown', sample('different phase')))
        report = comparison.compare(a, b)
        self.assertFalse(report['pairs'][0]['phase_deltas'])
        self.assertTrue(any('missing from candidate' in w for w in report['warnings']))
        self.assertTrue(any('storage markers' in w for w in report['warnings']))

    def test_repeated_phase_reports_worst_observed_headroom(self):
        body = sample() + sample(used=111000, free=1000, native_free=35000, largest=32000)
        phase = comparison.summarize(comparison.parse_log(capture(body=body)))[0]['phases']['http.after_load']
        self.assertEqual(phase['samples'], 2)
        self.assertEqual(phase['gc_free_min'], 1000)
        self.assertEqual(phase['gc_used_max'], 111000)
        self.assertEqual(phase['native_free_min'], 35004)
        self.assertEqual(phase['native_largest_min'], 32000)

    def test_metadata_without_samples_does_not_look_like_success(self):
        report = comparison.compare(comparison.parse_log(capture(body='')),
                                    comparison.parse_log(capture('frozen', body='')))
        self.assertTrue(any('no complete memory samples' in w for w in report['warnings']))

    def test_missing_native_region_does_not_understate_native_free(self):
        body = sample().replace('MEM_NATIVE 1 ', 'MEM_NATIVE 2 ')
        log = comparison.parse_log(capture(body=body))
        self.assertFalse(log['runs'][0]['samples'][0]['complete'])

    def test_bad_native_region_and_probe_error_invalidate_sample(self):
        for extra in ('MEM_NATIVE broken\n', 'MEM_NATIVE 1 113840 38416 36864 1508\n',
                      'MEM_ERROR allocation failed\n'):
            log = comparison.parse_log(capture(body=sample(end=False) + extra + 'MEM_END\n'))
            self.assertFalse(log['runs'][0]['samples'][0]['complete'])

    def test_probe_calls_actual_mem_info_without_forcing_collection(self):
        # Execute only report(), using spies so tests do not need ESP32 modules.
        node = next(n for n in ast.parse((ROOT / 'memory_probe.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'report')
        gc = types.SimpleNamespace(collect=Mock(), mem_free=Mock())
        mem_info = Mock()
        namespace = dict(gc=gc, micropython=types.SimpleNamespace(mem_info=mem_info),
            esp32=types.SimpleNamespace(HEAP_DATA=4, idf_heap_info=lambda _: [(100, 20, 10, 5)]),
            utime=types.SimpleNamespace(ticks_ms=lambda: 42))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'memory_probe.py', 'exec'), namespace)
        with patch('builtins.print') as output:
            namespace['report']('test phase')
        mem_info.assert_called_once_with()
        gc.collect.assert_not_called()
        gc.mem_free.assert_not_called()
        self.assertEqual(output.call_args_list[0].args, ('MEM_BEGIN', 42, 'test phase'))
        self.assertEqual(output.call_args_list[-1].args, ('MEM_END',))


if __name__ == '__main__':
    unittest.main()
