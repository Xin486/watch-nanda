"""Explicit, idempotent schema migration; never run on application import."""
import argparse
from sqlalchemy import inspect, text
from models.database import engine
from models.models import AgentCredential


def migrate(check_only=False):
    inspector = inspect(engine)
    columns = {c['name'] for c in inspector.get_columns('servers')}
    missing_column = 'collection_mode' not in columns
    missing_table = not inspector.has_table('agent_credentials')
    missing_password = 'ssh_password_encrypted' not in columns
    print('servers.collection_mode:', 'missing' if missing_column else 'present')
    print('agent_credentials:', 'missing' if missing_table else 'present')
    print('servers.ssh_password_encrypted:', 'missing' if missing_password else 'present')
    if check_only:
        print('Check only: no schema or data changes made.')
        return not (missing_column or missing_table or missing_password)
    if missing_column:
        with engine.begin() as connection:
            if engine.dialect.name == 'mysql':
                # Bound metadata lock waits; a blocked deploy should fail and be retried.
                connection.execute(text('SET SESSION lock_wait_timeout = 10'))
            connection.execute(text("ALTER TABLE servers ADD COLUMN collection_mode VARCHAR(10) NOT NULL DEFAULT 'ssh'"))
    AgentCredential.__table__.create(engine, checkfirst=True)
    if missing_password:
        with engine.begin() as connection:
            if engine.dialect.name == 'mysql':
                connection.execute(text('SET SESSION lock_wait_timeout = 10'))
            connection.execute(text('ALTER TABLE servers ADD COLUMN ssh_password_encrypted TEXT NULL'))
    print('Agent schema ready; existing nodes remain in SSH mode.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add the agent schema without altering historical data')
    parser.add_argument('--check', action='store_true', help='Inspect the schema without changing it')
    parser.add_argument('--require-ready', action='store_true', help='Read-only check, exit 2 if migration is required')
    args = parser.parse_args()
    ready = migrate(check_only=args.check or args.require_ready)
    if args.require_ready and not ready:
        raise SystemExit(2)
