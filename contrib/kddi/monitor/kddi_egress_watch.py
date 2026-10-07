#!/usr/bin/env python3
"""Quiet, bounded KDDI egress sampling via dedicated SOCKS; no direct fallback."""
import argparse
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

ROOT = Path('/var/lib/kddi-egress-monitor')
PROXY = 'socks5h://127.0.0.1:10840'
SOURCES = [('ipify', 'https://api.ipify.org'),
           ('cloudflare', 'https://www.cloudflare.com/cdn-cgi/trace')]

def probe():
    failures = []
    for source, url in SOURCES:
        start = time.monotonic()
        try:
            r = subprocess.run(['/usr/bin/curl', '--silent', '--show-error', '--fail',
                                '--ipv4', '--proxy', PROXY, '--noproxy', '',
                                '--connect-timeout', '6', '--max-time', '15',
                                '--header', 'Connection: close', url],
                               capture_output=True, text=True, timeout=18)
            if r.returncode:
                failures.append(f'{source}:curl_exit_{r.returncode}')
                continue
            raw = r.stdout.strip()
            if source == 'cloudflare':
                raw = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line).get('ip', '')
            ip = ipaddress.ip_address(raw)
            if ip.version != 4 or not ip.is_global:
                raise ValueError('not_global_ipv4')
            return str(ip), source, round((time.monotonic()-start)*1000), None
        except (ValueError, subprocess.TimeoutExpired) as exc:
            failures.append(f'{source}:{type(exc).__name__}')
    return None, None, None, ';'.join(failures)

def sample(db):
    at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    local = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    ip, source, ms, error = probe()
    old = db.execute('SELECT ip FROM samples WHERE ip IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    changed = bool(ip and old and ip != old[0])
    db.execute('INSERT INTO samples (utc,beijing,ip,source,elapsed_ms,error,changed) VALUES (?,?,?,?,?,?,?)',
               (at, local, ip, source, ms, error, int(changed)))
    db.commit()
    return {'beijing': local, 'ip': ip, 'source': source, 'changed': changed, 'error': error}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.samples <= 3:
        parser.error('samples must be between 1 and 3')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT/'sample.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        with sqlite3.connect(ROOT/'samples.sqlite3') as db:
            (ROOT/'samples.sqlite3').chmod(0o600)
            db.execute('CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, utc TEXT NOT NULL, beijing TEXT NOT NULL, ip TEXT, source TEXT, elapsed_ms INTEGER, error TEXT, changed INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS samples_utc ON samples(utc)')
            results = [sample(db) for _ in range(args.samples)]
        if args.report:
            print(json.dumps(results, ensure_ascii=False))
        return int(any(r['error'] for r in results))

if __name__ == '__main__':
    raise SystemExit(main())
