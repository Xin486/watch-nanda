"""Explicit, idempotent schema migration; never run on application import."""
import argparse
from sqlalchemy import text
from models.database import engine
from core.schema import reconcile


def migrate(check_only=False):
    with engine.begin() as connection:
        if engine.dialect.name == 'mysql' and not check_only:
            connection.execute(text('SET SESSION lock_wait_timeout = 10'))
        ready = reconcile(connection, check_only=check_only)
    if check_only:
        print('Check only: no schema or data changes made.')
    else:
        print('Monitoring schema ready; existing node IDs, credentials and history preserved.')
    return ready if check_only else True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add missing monitoring tables, columns and indexes without rewriting existing data')
    parser.add_argument('--check', action='store_true', help='Inspect the schema without changing it')
    parser.add_argument('--require-ready', action='store_true', help='Read-only check, exit 2 if migration is required')
    args = parser.parse_args()
    ready = migrate(check_only=args.check or args.require_ready)
    if args.require_ready and not ready:
        raise SystemExit(2)
