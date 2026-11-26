#!/usr/bin/env python3
"""Compare serial captures from the same workload on filesystem/frozen builds.

Usage: python3 tools/compare_memory.py baseline.log frozen.log --json result.json

Only complete MEM_BEGIN/MEM_END samples contribute to comparisons. The GC free
field comes directly from micropython.mem_info(), never from gc.mem_free().
Native free is summed across reported regions; largest is their maximum. Samples
are sequential observations, not atomic snapshots or proof of workload coverage.
"""
import argparse
import json
from pathlib import Path
import re

_GC = re.compile(r'GC:\s*total:\s*(\d+),\s*used:\s*(\d+),\s*free:\s*(\d+)'
                 r'(?:,\s*max new split:\s*(\d+))?')
_BEGIN = re.compile(r'MEM_BEGIN\s+(\d+)\s+(.+)')
_NATIVE = re.compile(r'MEM_NATIVE\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$')
_PHASES = {'before control modules': 'controls.before_load',
           'after control modules': 'controls.after_load',
           'HTTP first send stall': 'http.send_stall'}


def parse_log(text, source='<memory>'):
    """Return every run/sample, retaining malformed or truncated samples for audit."""
    result = {'source': str(source), 'runs': [], 'warnings': []}
    run = None
    sample = None
    inferred_phase = 'portal'

    def new_run(line_number, reason=None):
        return {'index': len(result['runs']) + 1, 'start_line': line_number,
                'reset': reason, 'build': None, 'storage': None, 'samples': [],
                'driver_oom': [], 'http_failures': []}

    def finish_sample(ended, reason=None):
        nonlocal sample
        if sample is None:
            return
        if reason:
            sample['errors'].append(reason)
        if sample['gc'] is None:
            sample['errors'].append('missing GC measurement')
        if not sample['native']:
            sample['errors'].append('missing native measurement')
        elif sorted(row['index'] for row in sample['native']) != list(range(len(sample['native']))):
            sample['errors'].append('missing or duplicated native region index')
        sample['complete'] = ended and not sample['errors']
        run['samples'].append(sample)
        if not sample['complete']:
            result['warnings'].append('line {}: incomplete sample ({})'.format(
                sample['line'], ', '.join(sample['errors'])))
        sample = None

    def finish_run():
        nonlocal run
        if run and (run['build'] or run['storage'] or run['samples'] or
                    run['driver_oom'] or run['http_failures']):
            result['runs'].append(run)
        run = None

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().removeprefix('>>> ').strip()
        # ESP boot banners and repeated firmware banners delimit captures that
        # contain multiple physical resets. A fresh empty boot is not a new run.
        reset = line.startswith('rst:') or line.startswith('ets ')
        firmware = line.startswith('Firmware build:')
        if reset or (firmware and run and (run['build'] or run['samples'])):
            finish_sample(False, 'reboot before MEM_END')
            finish_run()
            inferred_phase = 'portal'
            run = new_run(number, line if reset else 'new firmware banner')
        if run is None:
            run = new_run(number)
        if reset:
            run['reset'] = line
        if firmware:
            run['build'] = line.split(':', 1)[1].strip()
        if line.startswith('FW_STORAGE '):
            storage = line.split()[1]
            if run['storage'] and run['storage'] != storage:
                result['warnings'].append('line {}: conflicting storage marker'.format(number))
            run['storage'] = storage
        if 'mem_free before HTTP start:' in line:
            inferred_phase = 'http.before_load'
        elif 'mem_free after HTTP load:' in line:
            inferred_phase = 'http.after_load'
        elif line.startswith('AP station count:'):
            try:
                count = int(line.split(':', 1)[1])
                inferred_phase = 'ap.no_client' if count == 0 else 'ap.client_connected'
            except ValueError:
                pass
        if 'out of memory' in line.lower():
            run['driver_oom'].append({'line': number, 'message': line})
        if line.startswith('HTTP TX:') and ('event=retry-limit' in line or 'event=socket-error' in line):
            run['http_failures'].append({'line': number, 'message': line})
        begin = _BEGIN.fullmatch(line)
        if begin:
            finish_sample(False, 'new sample before MEM_END')
            context = begin.group(2)
            sample = {'line': number, 'ticks_ms': int(begin.group(1)), 'context': context,
                      'phase': inferred_phase if context == 'portal' else _PHASES.get(context, context),
                      'gc': None, 'native': [], 'errors': [], 'complete': False}
            continue
        if sample is None:
            continue
        if line == 'MEM_END':
            finish_sample(True)
            continue
        gc_match = _GC.fullmatch(line)
        if gc_match:
            if sample['gc'] is not None:
                sample['errors'].append('duplicate GC measurement')
            values = gc_match.groups()
            sample['gc'] = dict(total=int(values[0]), used=int(values[1]), free=int(values[2]),
                                max_new_split=None if values[3] is None else int(values[3]))
        elif line.startswith('GC:'):
            sample['errors'].append('malformed GC measurement')
        native = _NATIVE.fullmatch(line)
        if native:
            values = [int(value) for value in native.groups()]
            row = dict(zip(('index', 'total', 'free', 'largest', 'minimum'), values))
            if any(previous['index'] == row['index'] for previous in sample['native']):
                sample['errors'].append('duplicate native region')
            sample['native'].append(row)
        elif line.startswith('MEM_NATIVE'):
            sample['errors'].append('malformed native measurement')
        if line.startswith('MEM_ERROR'):
            sample['errors'].append(line)
    finish_sample(False, 'end of log before MEM_END')
    finish_run()
    if not result['runs']:
        result['warnings'].append('no firmware runs or memory samples found')
    return result


def summarize(log):
    """Keep repetitions separate; never silently pool different builds/storage."""
    summaries = []
    for run in log['runs']:
        phases = {}
        for sample in run['samples']:
            if sample['complete']:
                phases.setdefault(sample['phase'], []).append(sample)
        stats = {}
        for phase, samples in phases.items():
            stats[phase] = {
                'samples': len(samples),
                'gc_capacity_min': min(s['gc']['total'] for s in samples),
                'gc_capacity_max': max(s['gc']['total'] for s in samples),
                'gc_used_max': max(s['gc']['used'] for s in samples),
                'gc_free_min': min(s['gc']['free'] for s in samples),
                'native_free_min': min(sum(h['free'] for h in s['native']) for s in samples),
                'native_largest_min': min(max(h['largest'] for h in s['native']) for s in samples),
            }
        summaries.append({'run': run['index'], 'build': run['build'], 'storage': run['storage'],
                          'phases': stats, 'incomplete_samples': sum(not s['complete'] for s in run['samples']),
                          'driver_oom_count': len(run['driver_oom']),
                          'http_failure_count': len(run['http_failures'])})
    return summaries


def compare(baseline, candidate):
    """Pair runs by capture order, flag missing runs/phases instead of inventing data."""
    left, right = summarize(baseline), summarize(candidate)
    pairs, warnings = [], list(baseline['warnings']) + list(candidate['warnings'])
    for name, runs in (('baseline', left), ('candidate', right)):
        for run in runs:
            if not run['phases']:
                warnings.append('{} run {}: no complete memory samples'.format(name, run['run']))
    if len(left) != len(right):
        warnings.append('run counts differ; unmatched runs have no deltas')
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        pair = {'run_pair': index + 1, 'baseline': a, 'candidate': b, 'phase_deltas': {}}
        if a and b:
            for phase in sorted(set(a['phases']) | set(b['phases'])):
                av, bv = a['phases'].get(phase), b['phases'].get(phase)
                if av is None or bv is None:
                    warnings.append('run {}: phase {} missing from {}'.format(
                        index + 1, phase, 'baseline' if av is None else 'candidate'))
                    continue
                pair['phase_deltas'][phase] = {key: bv[key] - av[key] for key in av if key != 'samples'}
            if a['storage'] != 'filesystem' or b['storage'] != 'frozen':
                warnings.append('run {}: expected filesystem/frozen storage markers; observed {}/{}'.format(
                    index + 1, a['storage'], b['storage']))
        pairs.append(pair)
    return {'schema_version': 1, 'baseline': baseline, 'candidate': candidate,
            'pairs': pairs, 'warnings': warnings,
            'notes': ['Deltas are candidate minus baseline; runs pair by capture order.',
                      'GC free is existing managed free space; max_new_split is not added.',
                      'Sequential samples and logging have overhead; use identical instrumentation/workloads.',
                      'Reported native minima are observations, not a guaranteed reserve.']}


def render(report):
    lines = ['Memory comparison (bytes; candidate minus baseline)',
             'GC free excludes prospective native heap growth.']
    for pair in report['pairs']:
        lines.append('\nRun pair {}'.format(pair['run_pair']))
        for side in ('baseline', 'candidate'):
            run = pair[side]
            if run is None:
                lines.append('  {}: missing run'.format(side))
                continue
            lines.append('  {}: {} [{}], OOM={}, HTTP failures={}, incomplete={}'.format(
                side, run['build'], run['storage'], run['driver_oom_count'],
                run['http_failure_count'], run['incomplete_samples']))
        phases = sorted(set((pair['baseline'] or {}).get('phases', {})) |
                        set((pair['candidate'] or {}).get('phases', {})))
        for phase in phases:
            lines.append('  ' + phase)
            for key in ('gc_capacity_max', 'gc_used_max', 'gc_free_min', 'native_free_min', 'native_largest_min'):
                values = [((pair[side] or {}).get('phases', {}).get(phase) or {}).get(key)
                          for side in ('baseline', 'candidate')]
                delta = pair['phase_deltas'].get(phase, {}).get(key)
                lines.append('    {}: {} -> {} ({})'.format(
                    key, values[0], values[1], 'n/a' if delta is None else '{:+d}'.format(delta)))
    if report['warnings']:
        lines.append('\nWarnings:')
        lines.extend('  ' + warning for warning in report['warnings'])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--json', type=Path, help='Write complete parsed observations and comparison as JSON')
    args = parser.parse_args()
    report = compare(parse_log(args.baseline.read_text(errors='replace'), args.baseline),
                     parse_log(args.candidate.read_text(errors='replace'), args.candidate))
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
