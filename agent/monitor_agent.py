#!/usr/bin/env python3
"""Linux metrics-only agent. No listener, SSH keys, subprocesses or remote commands."""
import argparse
import json
import logging
import os
from pathlib import Path
import random
import ssl
import stat
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from uuid import uuid4

log = logging.getLogger('node-monitor-agent')


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
    before, idle_before = cpu_counters()
    time.sleep(1)
    after, idle_after = cpu_counters()
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    result = run(load_config(args.config), args.once)
    if args.once and not result:
        raise SystemExit(1)
