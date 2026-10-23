"""Find compatible runtimes without replacing the operating system Python/Node."""
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def packages(apt, dnf, enabled=True):
    if not enabled or os.geteuid() != 0:
        raise RuntimeError('缺少系统组件；请用 root 运行自动安装，或预装依赖后加 --no-bootstrap')
    if shutil.which('apt-get'):
        run(['apt-get', 'update'])
        run(['apt-get', 'install', '-y', '--no-install-recommends', *apt], env=dict(os.environ, DEBIAN_FRONTEND= 'noninteractive'))
    elif shutil.which('dnf'):
        run(['dnf', 'install', '-y', *dnf])
    else:
        raise RuntimeError('自动安装仅支持 apt/dnf；请手动准备依赖后使用 --no-bootstrap')


def compatible_python(path):
    if not path or not Path(path).is_file():
        return False
    try:
        return subprocess.run([str(path), '-c', 'import sys,venv;sys.exit(not ((3,10)<=sys.version_info[:2]<=(3,12)))'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def python_runtime(enabled=True):
    requested = os.getenv('PYTHON_BIN')
    if requested:
        requested = shutil.which(requested) or requested
        if not compatible_python(requested):
            raise RuntimeError('PYTHON_BIN 必须指向含 venv 的 Python 3.10–3.12')
        return str(Path(requested).absolute())
    candidates = [ROOT/'backend/.venv/bin/python', shutil.which('python3')]
    for version in ('3.12', '3.11', '3.10'):
        candidates += [shutil.which('python'+version)]
    for home in dict.fromkeys([Path.home(), Path('/root')]):
        candidates += sorted(home.glob('.pyenv/versions/3.1[012]*/bin/python3*'), reverse=True)
    for candidate in candidates:
        if compatible_python(candidate):
            # Do not build a replacement venv from the venv we will move away.
            result = run([candidate, '-c', 'import sys;print(sys._base_executable)'], capture_output=True, text=True)
            return result.stdout.strip()
    if not enabled:
        raise RuntimeError('未找到 Python 3.10–3.12；请设置 PYTHON_BIN 或允许自动准备运行时')
    print('准备项目专用 Python 3.12（不替换系统 Python）…', flush=True)
    try:
        run([sys.executable, '-m', 'pip', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        packages(['python3-pip', 'python3-venv', 'ca-certificates'], ['python3-pip', 'ca-certificates'], enabled)
    target = ROOT/'.runtime/bootstrap-tools'
    run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--target', target, 'uv'])
    env = dict(os.environ, PYTHONPATH=str(target), UV_PYTHON_INSTALL_DIR=str(ROOT/'.runtime/python'),
               UV_PYTHON_BIN_DIR=str(ROOT/'.runtime/bin'))
    run([sys.executable, '-m', 'uv', 'python', 'install', '3.12'], env=env)
    found = run([sys.executable, '-m', 'uv', 'python', 'find', '--managed-python', '3.12'],
                env=env, capture_output=True, text=True).stdout.strip()
    if not compatible_python(found):
        raise RuntimeError('项目专用 Python 校验失败')
    return found


def node_runtime(enabled=True):
    candidates = [os.getenv('WATCH_NANDA_NODE'), shutil.which('node')]
    candidates += [str(p) for p in sorted(Path.home().glob('.nvm/versions/node/v*/bin/node'), reverse=True)]
    candidates += [str(p) for p in sorted((ROOT/'.runtime').glob('node-v*/bin/node'), reverse=True)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                version = run([candidate, '--version'], capture_output=True, text=True, timeout=10).stdout.strip()
                if int(version.lstrip('v').split('.')[0]) >= 18 and (Path(candidate).parent/'npm').exists():
                    return str(Path(candidate).absolute())
            except (ValueError, OSError, subprocess.SubprocessError):
                continue
    if not enabled:
        raise RuntimeError('未找到 Node.js 18+ 和 npm')
    arch = {'x86_64':'x64', 'aarch64':'arm64'}.get(platform.machine())
    if not arch:
        raise RuntimeError('自动下载 Node 仅支持 x86_64/aarch64，请预装 Node.js 18+')
    base = 'https://nodejs.org/dist/latest-v24.x/'
    with urlopen(base+'SHASUMS256.txt', timeout=60) as response:
        checksums = response.read(100_000).decode()
    matches = re.findall(r'(?m)^([0-9a-f]{64})\s+(node-v[0-9.]+-linux-'+arch+r'\.tar\.gz)$', checksums)
    if len(matches) != 1:
        raise RuntimeError('官方 Node 校验清单格式无效')
    digest, name = matches[0]
    archive = ROOT/'.runtime'/name
    print('下载并校验项目专用 '+name, flush=True)
    with urlopen(base+name, timeout=120) as response, archive.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Node 下载 SHA256 校验失败')
    with tarfile.open(archive) as bundle:
        # Official archive is still checked before extraction, including symlinks.
        for member in bundle.getmembers():
            target = (ROOT/'.runtime'/member.name).resolve()
            if not target.is_relative_to((ROOT/'.runtime').resolve()) or member.isdev() or member.islnk():
                raise RuntimeError('Node 压缩包包含无效路径')
            if member.issym() and not (target.parent/member.linkname).resolve().is_relative_to((ROOT/'.runtime').resolve()):
                raise RuntimeError('Node 压缩包包含越界链接')
        bundle.extractall(ROOT/'.runtime')
    archive.unlink()
    return str(ROOT/'.runtime'/name.removesuffix('.tar.gz')/'bin/node')


def ensure_docker(enabled=True):
    if not shutil.which('docker'):
        if shutil.which('apt-get'):
            packages(['docker.io'], [], enabled)
        elif shutil.which('dnf'):
            if not enabled or os.geteuid() != 0:
                raise RuntimeError('缺少 Docker，请用 root 自动安装或手动准备')
            release = dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
            distro = release.get('ID','').strip('"')
            repository = 'fedora' if distro == 'fedora' else 'rhel' if distro == 'rhel' else 'centos'
            path = Path('/etc/yum.repos.d/watch-nanda-docker.repo')
            if not path.exists():
                with urlopen(f'https://download.docker.com/linux/{repository}/docker-ce.repo', timeout=60) as response:
                    content = response.read(100_000)
                if b'gpgcheck=1' not in content or b'https://download.docker.com/' not in content:
                    raise RuntimeError('Docker 官方仓库声明校验失败')
                path.write_bytes(content)
            run(['dnf','install','-y','docker-ce','docker-ce-cli','containerd.io'])
        else:
            raise RuntimeError('请预装 Docker，自动安装仅支持 apt/dnf')
    if subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        if enabled and os.geteuid() == 0 and Path('/run/systemd/system').is_dir():
            run(['systemctl', 'enable', '--now', 'docker'])
        else:
            raise RuntimeError('Docker 未运行；请启动 Docker 或配置可连接的 MySQL 8+')
