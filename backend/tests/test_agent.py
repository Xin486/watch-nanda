"""Agent security/state regressions, entirely isolated from production."""
import importlib.util
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_monitor
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from core.agent import Report, accept_report, token_digest
from core.status import freshness
from models.models import Server, ServerStats, AgentCredential, AlertConfig
from worker import tasks
import main
import migrate_agent


class AgentTests(unittest.TestCase):
    setUp = test_monitor.MonitorTests.setUp
    tearDown = test_monitor.MonitorTests.tearDown

    def node(self):
        with self.Session() as db:
            server = db.get(Server, 1)
            server.collection_mode = 'agent'
            server.latest_status_data = None
            db.add(AgentCredential(server_id=1, token_hash=token_digest('a'*43)))
            db.commit()

    def report(self, **overrides):
        return Report(**dict({
            'server_id': 1, 'report_id': str(uuid4()), 'sampled_at': self.now.isoformat()+'Z',
            'uptime_seconds': 100, 'cpu_percent': 40, 'cpu_cores': 4,
            'ram_total_mb': 100, 'ram_used_mb': 50, 'ram_percent': 50, 'gpu_data': [],
        }, **overrides))

    def test_wrong_token_node_and_revocation(self):
        self.node()
        with self.Session() as db:
            for report, token in [(self.report(), 'wrong'), (self.report(server_id=2), 'a'*43)]:
                with self.assertRaises(HTTPException) as error:
                    accept_report(db, report, token, self.now)
                self.assertEqual(error.exception.status_code, 401)
                db.rollback()
            db.delete(db.get(AgentCredential,1)); db.commit()
            with self.assertRaises(HTTPException): accept_report(db,self.report(),'a'*43,self.now)

    def test_retry_and_old_report_do_not_keep_node_online(self):
        self.node()
        report = self.report()
        with self.Session() as db:
            self.assertTrue(accept_report(db,report,'a'*43,self.now)['accepted'])
            original=db.get(Server,1).last_online
            self.assertEqual(accept_report(db,report,'a'*43,self.now+timedelta(minutes=5))['reason'],'duplicate')
            self.assertEqual(accept_report(db,self.report(sampled_at=(self.now-timedelta(seconds=1)).isoformat()+'Z'),'a'*43,self.now)['reason'],'out_of_order')
            self.assertEqual(db.get(Server,1).last_online,original)

    def test_timestamp_offset_clock_limits_and_rate(self):
        self.node()
        report=self.report(sampled_at=(self.now+timedelta(hours=8)).isoformat()+'+08:00')
        self.assertEqual(report.sampled_at,self.now)
        with self.Session() as db:
            accept_report(db,report,'a'*43,self.now)
            with self.assertRaises(HTTPException) as error:
                accept_report(db,self.report(sampled_at=(self.now+timedelta(seconds=1)).isoformat()+'Z'),'a'*43,self.now+timedelta(seconds=1))
            self.assertEqual(error.exception.status_code,429)
            for seconds in [61, -601]:
                db.get(Server,1).latest_status_data=None; db.commit()
                with self.assertRaises(HTTPException) as error:
                    accept_report(db,self.report(sampled_at=(self.now+timedelta(seconds=seconds)).isoformat()+'Z'),'a'*43,self.now)
                self.assertEqual(error.exception.status_code,422)

    def test_freshness_uses_receipt_and_offline_boundaries(self):
        self.node()
        with self.Session() as db:
            report=self.report(sampled_at=(self.now-timedelta(minutes=5)).isoformat()+'Z')
            accept_report(db,report,'a'*43,self.now)
            s=db.get(Server,1)
            for seconds, expected in [(720,'online'),(721,'stale'),(960,'stale'),(961,'offline')]:
                self.assertEqual(freshness(s,self.now+timedelta(seconds=seconds))[0],expected)

    def test_agent_never_enters_ssh_queue_and_inflight_ssh_is_discarded(self):
        self.node()
        with patch.object(tasks,'submit_server') as submit, patch.object(tasks,'collect_server_data') as collect:
            tasks.monitor_all_servers(); tasks._do_monitor_server(1)
            submit.assert_not_called(); collect.assert_not_called()
        with self.Session() as db:
            s=db.get(Server,1);s.collection_mode='ssh';s.latest_status_data={'cpu_percent':42};db.commit()
        def switch(config):
            with self.Session() as db:
                db.get(Server,1).collection_mode='agent';db.commit()
            return {'cpu_percent':99},True,''
        with patch.object(tasks,'collect_server_data',switch): tasks._do_monitor_server(1)
        with self.Session() as db: self.assertEqual(db.get(Server,1).latest_status_data['cpu_percent'],42)

    def test_history_deduplicates_sample_and_preserves_sample_time(self):
        self.node()
        stamp=self.now-timedelta(minutes=1)
        with self.Session() as db:
            accept_report(db,self.report(sampled_at=stamp.isoformat()+'Z'),'a'*43,self.now)
        tasks.flush_stats_to_db();tasks.flush_stats_to_db()
        with self.Session() as db:
            row=db.query(ServerStats).one()
            self.assertEqual(row.timestamp,stamp)
            s=db.get(Server,1);s.latest_status_data={**s.latest_status_data,'received_at':(self.now-timedelta(minutes=13)).isoformat()+'Z'};db.commit()
        tasks.flush_stats_to_db()
        with self.Session() as db:
            gap=db.query(ServerStats).filter(ServerStats.status=='stale').one()
            self.assertIsNone(gap.cpu_percent)

    def test_silence_alert_and_recovery(self):
        self.node()
        with self.Session() as db:
            db.add(AlertConfig(enable_email_alert=True,offline_threshold_minutes=5));db.commit()
            accept_report(db,self.report(sampled_at=(self.now-timedelta(minutes=17)).isoformat()+'Z'),'a'*43,self.now-timedelta(minutes=17))
        with patch.object(tasks,'send_email_wrapper',return_value=(True,'')) as send:
            tasks.check_server_alerts();send.assert_called_once()
            tasks.check_server_alerts();self.assertEqual(send.call_count,1)
            with self.Session() as db: accept_report(db,self.report(),'a'*43,self.now)
            tasks.check_server_alerts();self.assertEqual(send.call_count,2)
        with self.Session() as db: self.assertIsNone(db.get(Server,1).last_alert_time)

    def test_http_body_bound_schema_and_authentication(self):
        self.node()
        def session():
            with self.Session() as db: yield db
        main.app.dependency_overrides[main.get_db]=session
        client=TestClient(main.app)
        try:
            payload=self.report().model_dump(mode='json')
            # Report normalizes to UTC internally; wire timestamps must include the timezone.
            payload['sampled_at']=self.now.isoformat()+'Z'
            headers={'Authorization':'Bearer '+'a'*43}
            self.assertEqual(client.post('/api/agent/report',json=payload).status_code,401)
            self.assertEqual(client.post('/api/agent/report',content=b' '*65537,headers=headers).status_code,413)
            self.assertEqual(client.post('/api/agent/report',json={**payload,'command':'arbitrary'},headers=headers).status_code,422)
            self.assertEqual(client.post('/api/agent/report',json={**payload,'cpu_percent':101},headers=headers).status_code,422)
            response=client.post('/api/agent/report',json=payload,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['accepted'])
            self.assertFalse(client.post('/api/agent/report',json=payload,headers=headers).json()['accepted'])
            self.assertNotIn('token',json.dumps(client.get('/api/servers').json()))
        finally:
            client.close();main.app.dependency_overrides.clear()

    def test_mode_change_revokes_token(self):
        self.node()
        with self.Session() as db:
            main.update_server(1,main.ServerUpdate(hostname='test',ip_address='127.0.0.1',group_name='test',collection_mode='ssh'),db)
            self.assertIsNone(db.get(AgentCredential,1))
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')

    def test_migration_can_be_rerun(self):
        with patch.object(migrate_agent,'engine',self.engine):
            migrate_agent.migrate();migrate_agent.migrate()
        with self.Session() as db: self.assertEqual(db.get(Server,1).collection_mode,'ssh')

    def test_existing_database_migration_preserves_node_and_history(self):
        from sqlalchemy import text, inspect
        with self.engine.begin() as connection:
            connection.execute(text('DROP TABLE agent_credentials'))
            connection.execute(text('ALTER TABLE servers DROP COLUMN collection_mode'))
        with patch.object(migrate_agent,'engine',self.engine):
            migrate_agent.migrate(check_only=True)
            self.assertNotIn('collection_mode',{c['name'] for c in inspect(self.engine).get_columns('servers')})
            self.assertNotIn('agent_credentials',inspect(self.engine).get_table_names())
            migrate_agent.migrate();migrate_agent.migrate()
        self.assertIn('agent_credentials',inspect(self.engine).get_table_names())
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).hostname,'test')
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')

    def test_provision_rotate_and_revoke_without_exposing_digest(self):
        import io
        import manage_agent
        with patch.object(manage_agent,'SessionLocal',self.Session):
            with patch.object(sys,'argv',['manage_agent.py','issue','1','--url','https://monitor.invalid/api/agent/report']), patch('sys.stdout',new_callable=io.StringIO) as output:
                manage_agent.main()
            first=json.loads(output.getvalue())
            with self.Session() as db:
                self.assertEqual(db.get(AgentCredential,1).token_hash,token_digest(first['token']))
                accept_report(db,self.report(),first['token'],self.now)
            with patch.object(sys,'argv',['manage_agent.py','issue','1','--url','https://monitor.invalid/api/agent/report']), patch('sys.stdout',new_callable=io.StringIO) as output:
                manage_agent.main()
            second=json.loads(output.getvalue())
            self.assertNotEqual(first['token'],second['token'])
            with self.Session() as db:
                with self.assertRaises(HTTPException): accept_report(db,self.report(),first['token'],self.now)
            with patch.object(sys,'argv',['manage_agent.py','revoke','1']), patch('sys.stdout',new_callable=io.StringIO):
                manage_agent.main()
            with self.Session() as db:
                self.assertEqual(db.get(Server,1).collection_mode,'agent')
                self.assertIsNone(db.get(AgentCredential,1))
                with self.assertRaises(HTTPException): accept_report(db,self.report(),second['token'],self.now)

    def test_disabled_alerts_do_not_send_recovery_email(self):
        self.node()
        with self.Session() as db:
            accept_report(db,self.report(),'a'*43,self.now)
            db.get(Server,1).last_alert_time=self.now-timedelta(hours=1);db.commit()
        with patch.object(tasks,'send_email_wrapper') as send:
            tasks.check_server_alerts();send.assert_not_called()


spec=importlib.util.spec_from_file_location('monitor_agent',Path(__file__).resolve().parents[2]/'agent'/'monitor_agent.py')
agent=importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class CollectorTests(unittest.TestCase):
    def test_cpu_uses_two_samples_and_no_subprocess(self):
        files={'/proc/meminfo':'MemTotal: 2048 kB\nMemAvailable: 1024 kB\n','/proc/uptime':'100.5 100.0'}
        with patch.object(agent,'cpu_counters',side_effect=[(100,50),(200,70)]), patch.object(agent.time,'sleep'), patch.object(agent.Path,'read_text',lambda path: files[path.as_posix()]), patch.object(agent,'gpu_metrics',return_value=([],None)):
            result=agent.collect(1)
        self.assertEqual(result['cpu_percent'],80)
        self.assertEqual(result['ram_percent'],50)
        self.assertEqual(result['gpu_data'],[])

    def test_redirects_are_not_followed(self):
        with self.assertRaises(agent.HTTPError):
            agent.NoRedirect().redirect_request(agent.Request('https://monitor.invalid'),None,302,'redirect',{},'https://other.invalid')

    def test_retry_reuses_report_id(self):
        report={'report_id':str(uuid4()),'gpu_error':None}
        with patch.object(agent,'collect',return_value=report), patch.object(agent,'send',side_effect=[OSError,{'accepted':True}]) as send, patch.object(agent.time,'sleep'):
            agent.run({'server_id':1},once=True)
        self.assertEqual(send.call_args_list[0].args[1]['report_id'],send.call_args_list[1].args[1]['report_id'])

    def test_one_shot_failure_is_reported(self):
        with patch.object(agent,'collect',return_value={'gpu_error':None}), patch.object(agent,'send',side_effect=agent.HTTPError('https://monitor.invalid',401,'unauthorized',{},None)):
            self.assertFalse(agent.run({'server_id':1},once=True))


if __name__=='__main__': unittest.main()
