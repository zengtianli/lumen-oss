#!/usr/bin/env python3
"""Run isolated unittest files concurrently, preserving every result and log."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]

def worker(pattern, output):
    sys.path.insert(0, str(ROOT / 'engine'))
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'engine/tests'), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    record = {'file': pattern, 'total': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped)}
    record['ok'] = result.wasSuccessful() and result.testsRun > 0
    output.write_text(json.dumps(record, indent=2) + '\n')
    return 0 if record['ok'] else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--worker')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.output)
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    files = sorted((ROOT / 'engine/tests').glob('test_*.py'))
    if not files:
        parser.error('No unittest files discovered')
    output = ROOT / 'build/tests' / ('run-' + uuid.uuid4().hex)
    output.mkdir(parents=True)
    started = time.monotonic()
    def run(path):
        result_path = output / (path.stem + '.json')
        with (output / (path.stem + '.log')).open('w') as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                '--worker', path.name, '--output', str(result_path)], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT, timeout=180)
        result = json.loads(result_path.read_text())
        result['exit_code'] = process.returncode
        result['log'] = str(output / (path.stem + '.log'))
        return result
    results = []
    jobs = min(args.jobs, len(files))
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(run, path): path for path in files}
        for future in concurrent.futures.as_completed(pending):
            try:
                result = future.result()
            except Exception as error:
                result = {'file': pending[future].name, 'ok': False, 'error': str(error), 'total': 0}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {'ok': all(r['ok'] and r.get('exit_code') == 0 for r in results),
               'jobs': jobs, 'total': sum(r['total'] for r in results),
               'skipped': sum(r.get('skipped', 0) for r in results),
               'duration_seconds': round(time.monotonic() - started, 3),
               'files': sorted(results, key=lambda r: r['file']), 'output': str(output)}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
