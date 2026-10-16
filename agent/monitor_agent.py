#!/usr/bin/env python3
"""Linux metrics-only runtime; the bundled installer is a separate local root CLI."""
import argparse
import json
import logging
import os
try:
    import pwd
except ImportError:  # Allows configuration/contract tests on non-Linux build controllers.
    pwd = None
from pathlib import Path
import random
import ssl
import stat
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from uuid import uuid4

log = logging.getLogger('node-monitor-agent')


def process_snapshot(proc=Path('/proc')):
    """Read counters without command arguments, elevated permissions or subprocesses."""
    result = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / 'stat').read_text()
            # comm may contain spaces and parentheses; fields after the last ')' start at state.
            left, right = raw.index('('), raw.rindex(')')
            fields = raw[right + 2:].split()
            name = ''.join(c if c.isprintable() else '?' for c in raw[left+1:right])[:150]
            result[int(entry.name)] = (int(fields[19]), int(fields[11]) + int(fields[12]),
                                      max(0, int(fields[21])), entry.stat().st_uid, name)
        except (OSError, ValueError, IndexError):
            continue  # Processes may exit, and hidepid may restrict visibility.
        if len(result) >= 32768:
            break
    return result


def top_process(before, after, elapsed, total_kb):
    if not before or not after:
        return None
    hz = os.sysconf('SC_CLK_TCK')
    page_kb = os.sysconf('SC_PAGE_SIZE') / 1024
    candidates = []
    for pid, (started, ticks, rss, uid, name) in after.items():
        old = before.get(pid)
        if old is None or old[0] != started:
            continue  # Exclude PID reuse and processes without a complete sample.
        cpu = max(0, 100 * (ticks - old[1]) / hz / max(elapsed, 0.001))
        memory = min(100, 100 * rss * page_kb / max(total_kb, 1))
        candidates.append((cpu, memory, pid, uid, name))
    if not candidates:
        return None
    cpu, memory, pid, uid, name = max(candidates)
    try:
        user = pwd.getpwuid(uid).pw_name[:100]
    except (KeyError, AttributeError):
        user = str(uid)
    return {'pid': pid, 'user': user, 'name': name, 'cpu_percent': round(cpu, 2),
            'memory_percent': round(memory, 2)}


def cpu_counters():
    values = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    # guest counters are already included in user/nice.
    return sum(values[:8]), values[3] + (values[4] if len(values) > 4 else 0)


def gpu_metrics():
    try:
        import pynvml as nv
    except ImportError:
        error = 'NVML Python dependency unavailable' if Path('/dev/nvidia0').exists() else None
        return [], error
    initialized = False
    try:
        nv.nvmlInit()
        initialized = True
        result = []
        def optional(call):
            try:
                return round(float(call()), 2)
            except nv.NVMLError:
                return None
        def label(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)
        for i in range(nv.nvmlDeviceGetCount()):
            handle = nv.nvmlDeviceGetHandleByIndex(i)
            memory = nv.nvmlDeviceGetMemoryInfo(handle)
            temp = optional(lambda: nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU))
            result.append({
                'uuid': label(nv.nvmlDeviceGetUUID(handle)),
                'model': label(nv.nvmlDeviceGetName(handle)),
                'temp': f'{temp:g} C' if temp is not None else 'N/A',
                'memory_used': f'{memory.used // 1048576} MiB',
                'memory_total': f'{memory.total // 1048576} MiB',
                'memory_percent': round(memory.used / memory.total * 100, 2) if memory.total else 0,
                'gpu_util_percent': optional(lambda: nv.nvmlDeviceGetUtilizationRates(handle).gpu),
                'power_draw': optional(lambda: nv.nvmlDeviceGetPowerUsage(handle) / 1000),
                'power_limit': optional(lambda: nv.nvmlDeviceGetEnforcedPowerLimit(handle) / 1000),
            })
        return result, None
    except nv.NVMLError:
        return [], 'NVML metrics unavailable; check driver and device permissions'
    finally:
        if initialized:
            nv.nvmlShutdown()


def collect(server_id):
    try:
        processes_before = process_snapshot()
    except OSError:
        processes_before = {}
    before, idle_before = cpu_counters()
    started = time.monotonic()
    time.sleep(1)
    after, idle_after = cpu_counters()
    elapsed = time.monotonic() - started
    try:
        processes_after = process_snapshot()
    except OSError:
        processes_after = {}
    total = after - before
    if total <= 0:
        raise ValueError('CPU counters did not advance')
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        mem[key] = int(value.split()[0])
    used = max(0, mem['MemTotal'] - mem['MemAvailable'])
    gpus, error = gpu_metrics()
    return {
        'server_id': server_id, 'report_id': str(uuid4()),
        'sampled_at': datetime.now(timezone.utc).isoformat(),
        'uptime_seconds': int(float(Path('/proc/uptime').read_text().split()[0])),
        'cpu_percent': round(max(0, min(100, 100 * (1 - (idle_after - idle_before) / total))), 2),
        'cpu_cores': os.cpu_count() or 1,
        'ram_total_mb': mem['MemTotal'] // 1024, 'ram_used_mb': used // 1024,
        'ram_percent': round(used / mem['MemTotal'] * 100, 2),
        'gpu_data': gpus, 'gpu_error': error,
        'top_process_detail': top_process(processes_before, processes_after, elapsed, mem['MemTotal']),
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Redirects are not allowed', headers, fp)


def load_config(path):
    path = Path(path)
    mode = path.stat()
    if mode.st_mode & (stat.S_IWGRP | stat.S_IRWXO):
        raise ValueError('Config must not be group-writable or accessible to other users')
    config = json.loads(path.read_text())
    url = urlparse(config['url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('An HTTPS endpoint without URL credentials is required')
    if isinstance(config['server_id'], bool) or not isinstance(config['server_id'], int) or config['server_id'] < 1:
        raise ValueError('server_id must be a positive integer')
    if not isinstance(config['token'], str) or not 32 <= len(config['token']) <= 256:
        raise ValueError('Invalid node token')
    config.setdefault('interval_seconds', 300)
    config.setdefault('jitter_seconds', 15)
    if not 60 <= config['interval_seconds'] <= 300 or not 0 <= config['jitter_seconds'] <= 30:
        raise ValueError('Interval must be 60–300 seconds; jitter must be 0–30 seconds')
    return config


def send(config, report):
    context = ssl.create_default_context(cafile=config.get('ca_file'))
    if not config.get('ca_file') and not context.get_ca_certs():
        # Embedded Python may use a build-host CA path that is absent on this OS.
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    opener = build_opener(HTTPSHandler(context=context), NoRedirect())
    body = json.dumps(report, allow_nan=False).encode()
    if len(body) > 65536:
        raise ValueError('Metrics report exceeds 64 KiB')
    request = Request(config['url'], data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['token'],
    }, method='POST')
    with opener.open(request, timeout=15) as response:
        return json.loads(response.read(4096))


def run(config, once=False):
    # Spread first connection after a fleet restart, too.
    if not once:
        time.sleep(random.uniform(0, config['jitter_seconds']))
    while True:
        cycle = time.monotonic()
        completed = False
        try:
            report = collect(config['server_id'])
            if report['gpu_error']:
                log.warning('%s', report['gpu_error'])
            for attempt in range(3):
                try:
                    result = send(config, report)
                    log.info('Report completed: accepted=%s', result.get('accepted'))
                    completed = result.get('accepted') is True or result.get('reason') == 'duplicate'
                    break
                except HTTPError as error:
                    log.warning('Report rejected: HTTP %s', error.code)
                    if error.code < 500:
                        break
                except (URLError, TimeoutError, OSError):
                    log.warning('Report transport failed')
                if attempt < 2:
                    time.sleep((5, 15)[attempt] + random.uniform(0, 3))
        except Exception as error:
            # Never dump request objects or configuration credentials into logs.
            log.error('Collection/report failed: %s', type(error).__name__)
        if once:
            return completed
        delay = config['interval_seconds'] + random.uniform(0, config['jitter_seconds'])
        time.sleep(max(1, delay - (time.monotonic() - cycle)))


if __name__ == '__main__':
    if sys.argv[1:2] == ['--installer']:
        import install_node
        install_node.entrypoint(sys.argv[2:])
        raise SystemExit(0)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--self-test', action='store_true', help='Check embedded runtime/dependencies without reading credentials or reporting')
    args = parser.parse_args()
    if args.self_test:
        import platform
        import pynvml
        print(json.dumps({'ok':True,'frozen':bool(getattr(sys,'frozen',False)),'python':platform.python_version(),
                          'architecture':platform.machine(),'openssl':ssl.OPENSSL_VERSION}))
        raise SystemExit(0)
    if not args.config:
        parser.error('--config is required unless --self-test is used')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    result = run(load_config(args.config), args.once)
    if args.once and not result:
        raise SystemExit(1)
