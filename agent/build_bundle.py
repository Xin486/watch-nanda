"""Run only in the manylinux build container. No site credentials enter a bundle."""
import hashlib
from importlib.metadata import distribution,version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

SOURCE=Path('/source')
WORK=Path('/tmp/agent-build')
OUTPUT=Path('/output')


def shared_python():
    """manylinux's static interpreter omits libpython; build its exact-version library."""
    runtime=platform.python_version()
    name=f'libpython{sys.version_info.major}.{sys.version_info.minor}.so.1.0'
    target=Path(sys.base_prefix)/'lib'/name
    if target.exists(): return
    cache=Path('/cache')
    cached=cache/(platform.machine()+'-'+os.confstr('CS_GNU_LIBC_VERSION').replace(' ','-')+'-'+runtime+'-'+name)
    if not cached.exists():
        source=cache/f'Python-{runtime}.tar.xz'
        if not source.exists():
            print('Downloading official CPython source',runtime,flush=True)
            urllib.request.urlretrieve(f'https://www.python.org/ftp/python/{runtime}/Python-{runtime}.tar.xz',source)
        with tarfile.open(source) as archive:
            archive.extractall(WORK,filter='data')
        tree=WORK/f'Python-{runtime}'
        print('Building shared CPython library',runtime,flush=True)
        subprocess.run(['bash','configure','--enable-shared','--without-ensurepip','--disable-test-modules'],cwd=tree,check=True)
        subprocess.run(['make','-j2',name.removesuffix('.1.0')],cwd=tree,check=True)
        shutil.copyfile(tree/name,cached)
    shutil.copyfile(cached,target)


def main():
    WORK.mkdir()
    shared_python()
    subprocess.run([sys.executable,'-m','pip','install','--cache-dir','/cache/pip','-r',str(SOURCE/'requirements-build.txt'),
                    '-r',str(SOURCE/'requirements.txt')],check=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir',
                    '--name','node-monitor-agent','--hidden-import','pynvml','--hidden-import','install_node',
                    '--collect-data','certifi','--paths',str(SOURCE),
                    '--distpath',str(WORK/'dist'),'--workpath',str(WORK/'work'),'--specpath',str(WORK),
                    str(SOURCE/'monitor_agent.py')],check=True,cwd=WORK)
    bundle=WORK/'dist/node-monitor-agent'
    subprocess.run([str(bundle/'node-monitor-agent'),'--self-test'],check=True)
    notices=bundle/'licenses';notices.mkdir()
    source=Path('/cache')/f'Python-{platform.python_version()}.tar.xz'
    if source.exists():
        with tarfile.open(source) as archive:
            stream=archive.extractfile(f'Python-{platform.python_version()}/LICENSE')
            if stream: (notices/'Python.txt').write_bytes(stream.read())
    for package in ('pyinstaller','nvidia-ml-py','certifi'):
        dependency=distribution(package)
        for file in dependency.files or []:
            if file.name.upper().startswith(('LICENSE','COPYING')):
                (notices/(package+'-'+file.name)).write_bytes(dependency.locate_file(file).read_bytes())
    manifest={'format':1,'architecture':platform.machine(),'glibc_min':os.confstr('CS_GNU_LIBC_VERSION').split()[-1],
              'python':platform.python_version(),'build_image':os.environ.get('AGENT_BUILD_IMAGE'),
              'source_sha256':hashlib.sha256((SOURCE/'monitor_agent.py').read_bytes()).hexdigest(),
              'installer_sha256':hashlib.sha256((SOURCE/'install_node.py').read_bytes()).hexdigest(),
              'dependencies':{name:version(name) for name in ('pyinstaller','nvidia-ml-py','certifi')},
              'files':{path.relative_to(bundle).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted(bundle.rglob('*')) if path.is_file() and not path.is_symlink()}}
    (bundle/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    name=f'node-monitor-agent-linux-{platform.machine()}.tar.gz'
    archive=OUTPUT/name
    temporary=OUTPUT/(name+'.next')
    with tarfile.open(temporary,'w:gz',dereference=False) as stream:
        stream.add(bundle,arcname='node-monitor-agent')
    temporary.replace(archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (OUTPUT/(name+'.sha256')).write_text(digest+'  '+name+'\n')
    shutil.copyfile(bundle/'manifest.json',OUTPUT/(name+'.manifest.json'))
    print('BUILT',archive,'bytes',archive.stat().st_size,'SHA256',digest,flush=True)


if __name__=='__main__': main()
