"""Run locally by an administrator; never expose provisioning on unauthenticated APIs."""
import argparse
import json
import secrets
from urllib.parse import urlparse

from core.agent import token_digest
from models.database import SessionLocal
from models.models import AgentCredential, Server


def main():
    parser = argparse.ArgumentParser(description='Issue or revoke a metrics-only node token')
    parser.add_argument('action', choices=['issue', 'revoke'])
    parser.add_argument('server_id', type=int)
    parser.add_argument('--url', help='HTTPS report endpoint, required to issue')
    args = parser.parse_args()
    if args.action == 'issue':
        url = urlparse(args.url or '')
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            parser.error('--url must be an HTTPS endpoint without credentials, query or fragment')
    with SessionLocal() as db:
        server = db.query(Server).filter(Server.id == args.server_id).with_for_update().first()
        if not server:
            parser.error('Node not found')
        old = db.get(AgentCredential, args.server_id)
        if old:
            db.delete(old)
            db.flush()
        if args.action == 'revoke':
            server.latest_status_data = None
            server.status = 'pending'
            db.commit()
            print('Token revoked; SSH is not automatically enabled.')
            return
        token = secrets.token_urlsafe(32)
        db.add(AgentCredential(server_id=server.id, token_hash=token_digest(token)))
        server.collection_mode = 'agent'
        server.latest_status_data = None
        server.status = 'pending'
        server.offline_since = None
        server.last_alert_time = None
        db.commit()
        # Only this explicit command displays the token, once. Do not log its output.
        print(json.dumps({'url': args.url, 'server_id': server.id, 'token': token,
                          'interval_seconds': 300, 'jitter_seconds': 15}, indent=2))


if __name__ == '__main__':
    main()
