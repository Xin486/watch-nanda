"""Additive MySQL schema reconciliation against the shipped, reviewed DDL.

Never drop/rename columns, rewrite existing values, or invent missing identities.
The complete plan is checked before issuing DDL (MySQL DDL commits implicitly).
"""
from pathlib import Path
import re

SCHEMA = Path(__file__).resolve().parents[2] / 'monitor_db.sql'


def statements(content):
    content = re.sub(r'(?m)^\s*--.*$', '', content)
    for item in content.split(';'):
        if item.strip():
            yield item.strip()


def definitions(content):
    tables, indexes = {}, []
    for statement in statements(content):
        table = re.fullmatch(r'create\s+table\s+(\w+)\s*\((.*)\)', statement, re.I | re.S)
        index = re.fullmatch(r'create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(([^)]+)\)', statement, re.I | re.S)
        if table:
            # Current reviewed DDL has no commas in quoted defaults or nested types.
            columns = {}
            for part in table[2].split(','):
                name, definition = part.strip().split(None, 1)
                if not re.fullmatch(r'\w+', name):
                    raise RuntimeError('不支持的数据列声明')
                columns[name] = definition.strip()
            tables[table[1]] = (statement, columns)
        elif index:
            indexes.append((index[1], index[2], tuple(x.strip() for x in index[3].split(',')), statement))
        else:
            raise RuntimeError('初始化 SQL 包含未支持的语句，已停止，未忽略错误')
    return tables, indexes


def plan(connection, content=None):
    from sqlalchemy import inspect
    tables, indexes = definitions(SCHEMA.read_text(encoding='utf-8-sig') if content is None else content)
    inspector = inspect(connection)
    changes = []
    for name, (statement, columns) in tables.items():
        if not inspector.has_table(name):
            changes.append((name, statement))
            continue
        actual = {column['name'] for column in inspector.get_columns(name)}
        for column, definition in columns.items():
            if column in actual:
                continue
            # Missing PKs or required identity/token fields cannot be reconstructed
            # from existing rows. Nullable metrics remain NULL, rather than fake 0.
            identity = re.search(r'primary\s+key|auto_increment', definition, re.I)
            required = re.search(r'not\s+null', definition, re.I) and not re.search(r'\bdefault\b', definition, re.I)
            if identity or required:
                if connection.exec_driver_sql(f'SELECT 1 FROM `{name}` LIMIT 1').first():
                    raise RuntimeError(f'{name}.{column} 缺少身份/必填字段且已有数据，不能自动伪造；请从备份恢复该字段')
            if name == 'server_stats' and column not in ('id', 'server_id'):
                # Historical samples cannot acquire invented values/timestamps.
                # Future inserts retain the shipped default after the nullable add.
                default = re.search(r"\bdefault\s+('[^']*'|[\w.]+)", definition, re.I)
                added = re.sub(r"\bdefault\s+('[^']*'|[\w.]+)", '', definition, flags=re.I)
                changes.append((name + '.' + column, f'ALTER TABLE `{name}` ADD COLUMN `{column}` {added}'))
                if default:
                    changes.append((name+'.'+column+' default', f'ALTER TABLE `{name}` ALTER COLUMN `{column}` SET DEFAULT {default[1]}'))
            else:
                changes.append((name + '.' + column, f'ALTER TABLE `{name}` ADD COLUMN `{column}` {definition}'))
    for name, table, columns, statement in indexes:
        existing = inspector.get_indexes(table) if inspector.has_table(table) else []
        match = next((index for index in existing if index['name'] == name), None)
        if match:
            if tuple(match['column_names']) != columns or match.get('unique'):
                raise RuntimeError(f'索引 {name} 定义与项目不符；保留原索引，请人工核验')
        else:
            changes.append((name, statement))
    return changes


def reconcile(connection, content=None, check_only=False):
    changes = plan(connection, content)
    for name, statement in changes:
        print(('缺少：' if check_only else '补齐：') + name, flush=True)
        if not check_only:
            if connection.dialect.name == 'sqlite':
                # SQLite is used by isolated regressions, never by production.
                if re.search(r'ALTER COLUMN.*SET DEFAULT', statement, re.I):
                    continue
                statement = re.sub(r'\s+on update CURRENT_TIMESTAMP', '', statement, flags=re.I)
                statement = re.sub(r'\bint\s+auto_increment\s+primary key', 'INTEGER PRIMARY KEY AUTOINCREMENT', statement, flags=re.I)
                if statement.startswith('ALTER TABLE'):
                    statement = re.sub(r'\bdefault\s+CURRENT_TIMESTAMP', '', statement, flags=re.I)
            connection.exec_driver_sql(statement)
    if not changes:
        print('数据库表、字段及索引已齐全。', flush=True)
    return not changes
