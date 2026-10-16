"""Isolated regression tests: SQLite only, SSH/SMTP are mocked."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.gettempdir() + '/watch-nanda-test-bootstrap.sqlite'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from models.database import Base
from models.models import Server, ServerStats, EmailConfig, AlertConfig
from core.status import freshness, server_payload
from worker import tasks
import main


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.temp.name + '/test.db',
            poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=1,
            connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.patches = [patch.object(tasks, 'SessionLocal', self.Session), patch.object(main, 'SessionLocal', self.Session)]
        for p in self.patches: p.start()
        self.now = datetime.utcnow()
        with self.Session() as db:
            db.add(Server(id=1, hostname='test', ip_address='127.0.0.1', group_name='test',
                ssh_port=22, ssh_user='root', is_active=True, status='online',
                latest_status_data={'timestamp': self.now.isoformat()+'Z', 'cpu_percent': 42}))
            db.commit()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        tasks.running_tasks.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def test_four_states_and_boundary(self):
        with self.Session() as db:
            s = db.get(Server, 1)
            self.assertEqual(freshness(s, self.now)[0], 'online')
            self.assertEqual(freshness(s, self.now+timedelta(seconds=180))[0], 'online')
            self.assertEqual(freshness(s, self.now+timedelta(seconds=181))[0], 'stale')
            s.status = 'offline'
            self.assertEqual(freshness(s, self.now)[0], 'offline')
            s.latest_status_data = None
            self.assertEqual(freshness(s, self.now)[0], 'pending')

    def test_slow_ssh_releases_only_database_connection(self):
        entered, release = Event(), Event()
        def slow(config):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            entered.set()
            self.assertTrue(release.wait(5))
            return {'cpu_percent': 77}, True, ''
        with patch.object(tasks, 'collect_server_data', slow), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(tasks._do_monitor_server, 1)
            try:
                self.assertTrue(entered.wait(2))
                with self.Session() as db: self.assertEqual(db.get(Server, 1).hostname, 'test')
            finally: release.set()
            future.result(5)
        with self.Session() as db: self.assertEqual(db.get(Server, 1).latest_status_data['cpu_percent'], 77)

    def test_changed_configuration_discards_old_result(self):
        def collect(config):
            with self.Session() as db:
                db.get(Server, 1).ip_address = '127.0.0.2'
                db.commit()
            return {'cpu_percent': 99}, True, ''
        with patch.object(tasks, 'collect_server_data', collect): tasks._do_monitor_server(1)
        with self.Session() as db: self.assertEqual(db.get(Server, 1).latest_status_data['cpu_percent'], 42)

    def test_deleted_node_is_not_recreated(self):
        def collect(config):
            with self.Session() as db:
                db.delete(db.get(Server, 1)); db.commit()
            return {}, True, ''
        with patch.object(tasks, 'collect_server_data', collect): tasks._do_monitor_server(1)
        with self.Session() as db: self.assertIsNone(db.get(Server, 1))

    def test_queue_deduplicates_before_execution(self):
        with patch.object(tasks.executor, 'submit') as submit:
            tasks.submit_server(1); tasks.submit_server(1)
            self.assertEqual(submit.call_count, 1)

    def test_submit_failure_releases_reservation(self):
        with patch.object(tasks.executor, 'submit', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError): tasks.submit_server(1)
        self.assertNotIn(1, tasks.running_tasks)

    def test_failure_and_recovery_share_state_machine(self):
        with self.Session() as db:
            db.add(AlertConfig(enable_email_alert=True)); db.commit()
        with patch.object(tasks, 'collect_server_data', return_value=({}, False, 'timeout')):
            tasks._do_monitor_server(1)
        with self.Session() as db:
            s=db.get(Server, 1)
            self.assertEqual(s.status, 'offline'); self.assertIsNotNone(s.offline_since)
            s.last_alert_time=self.now; db.commit()
        with patch.object(tasks, 'collect_server_data', return_value=({}, True, '')), patch.object(tasks, 'send_email_wrapper', return_value=(True,'')) as send:
            tasks._do_monitor_server(1)
            send.assert_called_once()
        with self.Session() as db:
            s=db.get(Server, 1)
            self.assertEqual(s.status, 'online'); self.assertIsNone(s.offline_since); self.assertIsNone(s.last_alert_time)

    def test_stale_history_is_gap_not_repeated_measurement(self):
        with self.Session() as db:
            db.get(Server,1).latest_status_data={'timestamp':(self.now-timedelta(minutes=10)).isoformat()+'Z','cpu_percent':99}
            db.commit()
        tasks.flush_stats_to_db()
        with self.Session() as db:
            row=db.query(ServerStats).one(); self.assertEqual(row.status,'stale')
            points=main.get_server_history(1,1,db)['data']
            self.assertIsNone(points[0]['cpu']); self.assertIsNone(points[0]['gpu'])
            points=main.get_server_history(1,90,db)['data']; self.assertIsNone(points[0]['cpu'])

    def test_stale_offline_does_not_send_node_alert(self):
        with self.Session() as db:
            db.add(AlertConfig(enable_email_alert=True,offline_threshold_minutes=1))
            s=db.get(Server,1); s.status='offline'; s.offline_since=self.now-timedelta(hours=1)
            s.latest_status_data={'timestamp':(self.now-timedelta(minutes=10)).isoformat()+'Z'}
            db.commit()
        with patch.object(tasks,'send_email_wrapper') as send:
            tasks.check_server_alerts(); send.assert_not_called()

    def test_email_read_redacts_and_omission_preserves_password(self):
        with self.Session() as db:
            db.add(EmailConfig(smtp_password='test-only-secret',smtp_server='smtp.test')); db.commit()
            self.assertNotIn('smtp_password',main.get_email_settings(db))
            self.assertTrue(main.get_email_settings(db)['has_password'])
            main.update_email_settings(main.EmailUpdate(smtp_server='updated.test'),db)
            self.assertEqual(db.query(EmailConfig).one().smtp_password,'test-only-secret')

    def test_rest_and_sse_use_same_contract(self):
        with self.Session() as db: rest=main.get_servers(db=db)
        async def first_event():
            response=await main.stream_servers()
            try: return json.loads((await response.body_iterator.__anext__())[6:])
            finally: await response.body_iterator.aclose()
        stream=asyncio.run(first_event())
        for key in ('id','status','collection_status','cpu_percent','stale_after_seconds'):
            self.assertEqual(rest[0][key],stream[0][key])

    def test_connection_edit_resets_freshness(self):
        with self.Session() as db:
            main.update_server(1,main.ServerUpdate(hostname='test',ip_address='127.0.0.2',group_name='test',ssh_user='root',ssh_port=22),db)
            self.assertEqual(server_payload(db.get(Server,1))['status'],'pending')


if __name__ == '__main__': unittest.main()
