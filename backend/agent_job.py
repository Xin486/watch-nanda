"""Isolated deployment worker. Secrets enter only through a private stdin pipe."""
import argparse
import json
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from install_agent import install, default_report_url, InstallError


def interrupted(*_):
    # Let the installer's BaseException handler restore credentials and remote service.
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, interrupted)
    data = json.loads(sys.stdin.buffer.read(16384))
    args = argparse.Namespace(ip=data['host'], port=data['port'], user=data['username'],
        server_id=data['server_id'], url=default_report_url(data['host']),
        ca_file=Path('/etc/node-monitor/tls/ca.crt'), bundle=None, source=False, check_only=False, require_bundle=True,
        reinstall=bool(data.get('reinstall', False)))
    if not list((ROOT / 'agent' / 'dist').glob('node-monitor-agent-linux-*.tar.gz')):
        raise InstallError('中心缺少独立 Agent 包，请先运行 agent/build.sh')
    result = install(args, data['password'], known_hosts_only=True)
    print('JOB_RESULT ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('部署失败：' + (str(error) if isinstance(error, InstallError) else type(error).__name__), flush=True)
        raise SystemExit(1)
