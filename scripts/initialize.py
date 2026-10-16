"""Database bootstrap shared by setup.sh. Existing data and credentials are preserved."""
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def prepare_env(root=ROOT):
    path = root / 'backend' / '.env'
    if path.exists():
        print('保留已有 backend/.env（不修改数据库密码）。', flush=True)
        return
    content = (root / 'backend' / '.env.example').read_text(encoding='utf-8-sig')
    password = secrets.token_urlsafe(32)
    content, count = re.subn(r'(?m)^DB_PASSWORD=.*$', 'DB_PASSWORD=' + password, content)
    if count != 1:
        raise RuntimeError('.env.example 必须包含且仅包含一个 DB_PASSWORD 配置项')
    # Exclusive creation avoids overwriting a configuration created concurrently.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content.replace('\r\n', '\n'))
    print('已生成权限 600 的 backend/.env，数据库密码随机生成且不输出。', flush=True)


def quoted_identifier(value):
    if not value or '\x00' in value or len(value) > 64:
        raise RuntimeError('数据库名称为空或不合法')
    return '`' + value.replace('`', '``') + '`'


def connect_server(url):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    # URL.set(database=None) preserves the old value rather than clearing it.
    server_url = URL.create(url.drivername, username=url.username, password=url.password,
                            host=url.host, port=url.port, query=url.query)
    return create_engine(server_url, connect_args={
        'connect_timeout': 5, 'read_timeout': 15, 'write_timeout': 15,
    }, hide_parameters=True)


def docker_database(url):
    if url.host not in ('localhost', '127.0.0.1', '::1'):
        raise RuntimeError('远程数据库无法连接；请检查地址、网络及权限，不会创建本地替代库')
    try:
        with socket.create_connection((url.host, url.port or 3306), timeout=2):
            raise RuntimeError('数据库端口已被占用但查询失败；请检查原有数据库与凭据，不会创建新容器')
    except ConnectionRefusedError:
        pass
    except socket.timeout:
        raise RuntimeError('本地数据库端口探测超时；请先核对端口，不会创建替代容器') from None
    if not shutil.which('docker'):
        raise RuntimeError('无可连接的本地 MySQL。请安装/启动 Docker，或在 backend/.env 指定已有 MySQL 8+；远程数据库不要求 Docker')
    subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
    name = os.environ.get('MYSQL_CONTAINER', 'watch-nanda-mysql')
    volume = os.environ.get('MYSQL_VOLUME', 'watch-nanda-mysql-data')
    image = os.environ.get('MYSQL_IMAGE', 'mysql:8.0')
    if not url.password:
        raise RuntimeError('自动创建数据库需要非空 DB_PASSWORD')
    import json
    existing = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=15)
    if existing.returncode == 0:
        info = json.loads(existing.stdout)[0]
        bindings = info.get('HostConfig', {}).get('PortBindings', {}).get('3306/tcp') or []
        if not any(int(b['HostPort']) == (url.port or 3306) for b in bindings):
            raise RuntimeError('指定 MySQL 容器的端口与 .env 不一致；保留原容器，请先核对配置')
        print('复用已有 MySQL 容器：' + name, flush=True)
        if not info['State']['Running']:
            subprocess.run(['docker', 'start', name], check=True, stdout=subprocess.DEVNULL, timeout=30)
    else:
        if 'no such' not in existing.stderr.lower():
            raise RuntimeError('无法确认 MySQL 容器是否存在；不会尝试创建替代容器')
        print('创建本地 MySQL 容器：' + name + '（默认仅绑定回环地址）', flush=True)
        env = dict(os.environ)
        env['MYSQL_DATABASE'] = url.database
        env['MYSQL_ROOT_PASSWORD'] = url.password if url.username == 'root' else secrets.token_urlsafe(32)
        args = ['docker', 'run', '-d', '--name', name, '--restart', 'unless-stopped',
                '-e', 'MYSQL_ROOT_PASSWORD', '-e', 'MYSQL_DATABASE']
        if url.username != 'root':
            env.update(MYSQL_USER=url.username, MYSQL_PASSWORD=url.password)
            args += ['-e', 'MYSQL_USER', '-e', 'MYSQL_PASSWORD']
            runtime = ROOT / '.runtime'
            runtime.mkdir(mode=0o700, exist_ok=True)
            admin = runtime / 'mysql-admin.env'
            if admin.exists():
                value = admin.read_text().strip().removeprefix('MYSQL_ROOT_PASSWORD=')
                if not value:
                    raise RuntimeError('已保存的数据库管理密码为空，请核对 .runtime/mysql-admin.env')
                env['MYSQL_ROOT_PASSWORD'] = value
            else:
                fd = os.open(admin, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    stream.write('MYSQL_ROOT_PASSWORD=' + env['MYSQL_ROOT_PASSWORD'] + '\n')
        bind = '[::1]' if url.host == '::1' else '127.0.0.1'
        args += ['-p', f'{bind}:{url.port or 3306}:3306', '-v', volume + ':/var/lib/mysql', image,
                 '--character-set-server=utf8mb4', '--collation-server=utf8mb4_unicode_ci']
        subprocess.run(args, env=env, check=True, stdout=subprocess.DEVNULL)
    server = connect_server(url)
    try:
        for attempt in range(60):
            try:
                with server.connect() as connection:
                    connection.exec_driver_sql('SELECT 1')
                return
            except Exception:
                if attempt % 10 == 0:
                    print(f'等待 MySQL 就绪（{attempt * 2}/120 秒）…', flush=True)
                time.sleep(2)
        raise RuntimeError('MySQL 就绪超时。检查容器日志、配置和原数据卷；不要删除数据卷来修复密码')
    finally:
        server.dispose()


def schema_statements(content):
    """The shipped schema contains only CREATE TABLE/INDEX, no routines or delimiters."""
    content = re.sub(r'(?m)^\s*--.*$', '', content)
    for statement in content.split(';'):
        if statement.strip():
            yield statement.strip()


def apply_schema(connection, sql):
    from sqlalchemy import inspect
    for statement in schema_statements(sql):
        table = re.match(r'create\s+table\s+(\w+)\s*\(', statement, re.I)
        index = re.match(r'create\s+index\s+(\w+)\s+on\s+(\w+)', statement, re.I)
        if table:
            inspector = inspect(connection)
            if inspector.has_table(table[1]):
                continue
            print('创建数据表：' + table[1], flush=True)
        elif index:
            inspector = inspect(connection)
            if index[1] in {i['name'] for i in inspector.get_indexes(index[2])}:
                continue
            print('创建索引：' + index[1], flush=True)
        else:
            raise RuntimeError('初始化 SQL 包含未支持的语句，已停止，未忽略错误')
        connection.exec_driver_sql(statement)


def initialize():
    prepare_env()
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError
    from core.config import settings
    url = make_url(settings.DATABASE_URL)
    if url.drivername != 'mysql+pymysql' or not url.database or not url.username:
        raise RuntimeError('需要 mysql+pymysql 的 DATABASE_URL，或完整的 DB_HOST/DB_USER/DB_NAME 配置')
    print(f'准备数据库：{url.host}:{url.port or 3306}/{url.database}', flush=True)
    server = connect_server(url)
    try:
        try:
            with server.connect() as connection:
                connection.exec_driver_sql('SELECT 1')
        except OperationalError as error:
            if getattr(error.orig, 'args', [None])[0] != 2003:
                raise RuntimeError('数据库认证或权限错误；不会覆盖凭据或重建容器') from None
            docker_database(url)
        with server.begin() as connection:
            version = str(connection.exec_driver_sql('SELECT VERSION()').scalar())
            if 'mariadb' in version.lower() or int(version.split('.')[0]) < 8:
                raise RuntimeError('项目的历史 JSON_TABLE 查询要求 MySQL 8+，当前版本不支持')
            connection.exec_driver_sql('CREATE DATABASE IF NOT EXISTS ' + quoted_identifier(url.database)
                                      + ' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    finally:
        server.dispose()
    engine = create_engine(url, hide_parameters=True, connect_args={'connect_timeout': 5})
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('SET SESSION lock_wait_timeout = 10')
            apply_schema(connection, (ROOT / 'monitor_db.sql').read_text(encoding='utf-8-sig'))
        from migrate_agent import migrate
        migrate()
        from models.models import Server, ServerStats, EmailConfig, AlertConfig, AgentCredential
        inspector = inspect(engine)
        for model in (Server, ServerStats, EmailConfig, AlertConfig, AgentCredential):
            expected = {c.name for c in model.__table__.columns}
            actual = {c['name'] for c in inspector.get_columns(model.__tablename__)}
            if not expected <= actual:
                raise RuntimeError('表结构不完整：' + model.__tablename__ + ' 缺少 ' + ','.join(sorted(expected-actual)))
        print('5 张核心表及 Agent 迁移校验通过，已有节点和历史保留。', flush=True)
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        initialize()
    except (Exception, KeyboardInterrupt) as error:
        # SQLAlchemy/Docker errors can contain connection parameters; do not dump them.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('数据库初始化失败：' + message, file=sys.stderr)
        raise SystemExit(1)
