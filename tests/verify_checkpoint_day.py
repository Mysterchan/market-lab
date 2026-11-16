"""Full-file checkpoint verification: verified baseline, SIGKILL/restart, exact CSV/core equality.

Writes CSVs, checkpoints, recovery journals, logs and report.json to a NEW directory.
Input is never changed. No Nasdaq data is included in this script.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def checkpoint(path):
    data = path.read_bytes()
    body, checksum = data[:-64], data[-64:]
    if hashlib.sha256(body).hexdigest().encode() != checksum:
        raise AssertionError('checkpoint SHA-256 mismatch')
    position = 0

    def take(n):
        nonlocal position
        value = body[position:position+n]
        if len(value) != n:
            raise AssertionError('truncated checkpoint')
        position += n
        return value

    def number(width=8):
        return int.from_bytes(take(width), 'big')

    def field():
        return take(number())

    assert take(8) == b'ITCHCP01'
    version = number(4)
    assert version in (1, 2)
    result = {'input_bytes': number(), 'input_sha256': field().decode(), 'symbol': field().decode()}
    for name in ('next_frame', 'offset', 'selected', 'csv_fnv1a', 'depth'):
        result[name] = number()
    for name in ('with_offset', 'allow_implicit_eof', 'last_end_messages', 'has_locate'):
        result[name] = number(1)
        assert result[name] in (0, 1)
    result['locate'] = number(2)
    result['live_orders'] = number()
    take(result['live_orders'] * 17)
    for name in ('bid_levels', 'ask_levels'):
        result[name] = number()
        take(result[name] * 12)
    # Include every order/level byte, but exclude the run-specific CSV journal binding.
    core = body[:8] + struct.pack('>I', 1) + body[12:position]
    result['core_sha256'] = hashlib.sha256(core).hexdigest()
    if version == 2:
        result['csv_path'] = field().decode()
        result['csv_bytes'] = number()
    assert position == len(body)
    result['frames'] = result['next_frame'] - 1
    return result, core


def exact_csv_equal(left, right):
    rows = 0
    digest = hashlib.sha256()
    with left.open('rb') as a, right.open('rb') as b:
        while True:
            x, y = a.read(1024 * 1024), b.read(1024 * 1024)
            if x != y:
                raise AssertionError('full CSV bytes differ')
            if not x:
                break
            rows += x.count(b'\n')
            digest.update(x)
    return rows - 1, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--exe', type=Path, default=Path('build/itch_replay'))
    parser.add_argument('--symbol', default='AAPL')
    parser.add_argument('--depth', type=int, default=5)
    parser.add_argument('--interval', type=int, default=10000000)
    parser.add_argument('--interrupt-at', type=int, nargs='+', default=[20000000, 130000000, 250000000])
    parser.add_argument('--allow-implicit-eof', action='store_true')
    args = parser.parse_args()
    assert args.interval > 0
    assert args.interrupt_at == sorted(set(args.interrupt_at)) and args.interrupt_at[0] > 0
    source, root = args.input.resolve(), args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    base = [str(args.exe.resolve()), '--input', str(source), '--symbol', args.symbol,
            '--depth', str(args.depth), '--with-offset']
    if args.allow_implicit_eof:
        base.append('--allow-implicit-eof')
    baseline_csv, baseline_cp = root / 'baseline.csv', root / 'baseline.cp'
    recovered_csv, recovered_cp = root / 'recovered.csv', root / 'recovered.cp'
    report = {'status': 'running', 'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'input': str(source), 'interval': args.interval, 'interruptions': [], 'runs': []}

    def save_report():
        temporary = root / 'report.json.tmp'
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(root / 'report.json')

    def run(name, command, target=None):
        print(f'{name}: starting' + (f'; SIGKILL after saved frame >= {target}' if target else ''), flush=True)
        start = time.monotonic()
        with (root / f'{name}.stdout.log').open('wb') as out, (root / f'{name}.stderr.log').open('wb') as err:
            process = subprocess.Popen(command, stdout=out, stderr=err)
            last_stamp, state, next_status = None, None, start + 30
            try:
                while process.poll() is None:
                    if target and recovered_cp.exists():
                        stat = recovered_cp.stat()
                        stamp = (stat.st_mtime_ns, stat.st_size)
                        if stamp != last_stamp:
                            state, _ = checkpoint(recovered_cp)
                            last_stamp = stamp
                        if state and state['frames'] >= target:
                            process.kill()
                            code = process.wait(timeout=10)
                            assert code == -9, f'expected SIGKILL, got {code}'
                            state, _ = checkpoint(recovered_cp)
                            journal = Path(state['csv_path'])
                            assert journal.stat().st_size >= state['csv_bytes']
                            assert not recovered_csv.exists(), 'partial CSV was published'
                            report['interruptions'].append({
                                'requested_frame': target, 'saved_checkpoint': state,
                                'journal_bytes_after_kill': journal.stat().st_size,
                                'returncode': code})
                            print(f'{name}: killed; recoverable frame={state["frames"]}, '
                                  f'CSV rows={state["selected"]}, live orders={state["live_orders"]}', flush=True)
                            break
                    now = time.monotonic()
                    if now >= next_status:
                        suffix = f'; saved frame={state["frames"]}' if state else ''
                        print(f'{name}: running {now-start:.0f}s{suffix}', flush=True)
                        next_status = now + 30
                    time.sleep(0.05)
                else:
                    if target:
                        raise AssertionError(f'{name}: process finished before interruption: {process.returncode}')
                    if process.returncode:
                        raise AssertionError((root / f'{name}.stderr.log').read_text())
                report['runs'].append({'name': name, 'seconds': time.monotonic()-start,
                                      'returncode': process.returncode, 'command': command})
                save_report()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        print(f'{name}: finished after {time.monotonic()-start:.1f}s', flush=True)

    save_report()
    try:
        run('baseline', base + ['--output', str(baseline_csv), '--checkpoint-out', str(baseline_cp), '--verify'])
        baseline, baseline_core = checkpoint(baseline_cp)
        assert args.interrupt_at[-1] < baseline['frames']
        report['baseline'] = baseline
        save_report()
        periodic = ['--output', str(recovered_csv), '--checkpoint-out', str(recovered_cp),
                    '--checkpoint-every-frames', str(args.interval)]
        for index, target in enumerate(args.interrupt_at):
            command = base + periodic
            if index:
                command += ['--resume', str(recovered_cp), '--resume-csv']
            run(f'interruption-{index+1}', command, target)
        run('final-resume', base + periodic + ['--resume', str(recovered_cp), '--resume-csv', '--verify'])
        recovered, recovered_core = checkpoint(recovered_cp)
        assert recovered_core == baseline_core, 'final checkpoint state differs'
        rows, csv_digest = exact_csv_equal(baseline_csv, recovered_csv)
        assert rows == baseline['selected'] == recovered['selected']
        print('Comparing full input SHA-256 with independent hashlib', flush=True)
        input_digest = file_sha256(source)
        assert input_digest == baseline['input_sha256'] == recovered['input_sha256']
        report.update(status='passed', recovered=recovered, csv_rows=rows, csv_sha256=csv_digest,
                      csv_bytes=baseline_csv.stat().st_size, input_sha256=input_digest,
                      csv_exact_equal=True, checkpoint_core_exact_equal=True)
        save_report()
        print(f'PASS: frames={baseline["frames"]}, rows={rows}, '
              f'CSV SHA-256={csv_digest}; final checkpoint core identical', flush=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save_report()
        raise


if __name__ == '__main__':
    main()
