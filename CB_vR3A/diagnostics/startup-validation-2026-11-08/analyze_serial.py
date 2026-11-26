"""Extract complete checkpoint records, preserving runs and raw native regions."""
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = []
current = None
run = 0
for line in path.read_bytes().decode('utf-8', 'replace').splitlines():
    if line.startswith('rst:') or 'MPY: soft reboot' in line:
        run += 1
    if line.startswith('MEM_BEGIN '):
        if current:
            rows.append(current)
        fields = line.split(maxsplit=2)
        current = dict(run=run, ticks=fields[1], context=fields[2], native=[], complete=False)
    elif current:
        match = re.search(r'GC: total: (\d+), used: (\d+), free: (\d+)(?:, max new split: (\d+))?', line)
        if match:
            current.update(zip(('gc_total', 'gc_used', 'gc_free', 'gc_max_new_split'),
                               (int(x) if x is not None else None for x in match.groups())))
        if line.startswith('stack:'):
            current['stack'] = line
        if line.startswith('MEM_NATIVE '):
            current['native'].append([int(x) for x in line.split()[1:]])
        if line == 'MEM_END':
            current['complete'] = 'gc_total' in current and bool(current['native'])
            if current['native']:
                current['native_free'] = sum(r[2] for r in current['native'])
                current['native_largest'] = max(r[3] for r in current['native'])
            rows.append(current)
            current = None
if current:
    rows.append(current)
output = path.with_suffix('.checkpoints.json')
output.write_text(json.dumps(rows, indent=2) + '\n')
previous = None
for row in rows:
    if not row['complete']:
        print('INCOMPLETE', row['context'])
        continue
    if previous and previous['run'] == row['run']:
        growth = row['gc_total'] - previous['gc_total']
        loss = previous['native_free'] - row['native_free']
        if growth or loss > 4096:
            print('CHANGE', previous['context'], '->', row['context'],
                  'GC capacity delta', growth, 'native free delta', -loss)
    previous = row
print('Saved', len(rows), 'checkpoints:', output)
