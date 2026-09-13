"""Isolated history correctness tests; no production writes."""
import unittest
from datetime import datetime, timedelta
from test_monitor import MonitorTests as _Fixtures
from models.models import ServerStats
from core.history import history_payload, gpu_percent, percent


class HistoryTests(unittest.TestCase):
    setUp = _Fixtures.setUp
    tearDown = _Fixtures.tearDown

    def row(self, stamp, cpu=0, ram=0, gpu=None, status='online'):
        return ServerStats(server_id=1, timestamp=stamp, cpu_percent=cpu,
                           ram_percent=ram, gpu_data=gpu, status=status)

    def test_valid_zero_missing_invalid_and_gpu_average(self):
        self.assertEqual(percent(0), 0)
        for value in (None, '5', True, float('nan'), float('inf'), -1, 101):
            self.assertIsNone(percent(value))
        self.assertIsNone(gpu_percent([]))
        self.assertEqual(gpu_percent([{}, {'memory_percent':20}, {'memory_percent':40}, None]), 30)

    def test_daily_beijing_boundary_and_sample_weighted_summary(self):
        end=datetime(2026,9,13,12)
        with self.Session() as db:
            db.add_all([
                self.row(datetime(2026,9,11,15,50),10,20,[{'memory_percent':30}]),
                self.row(datetime(2026,9,11,16,0),60,40,[]),
                self.row(datetime(2026,9,11,16,10),100,60,[{'memory_percent':90}]),
                self.row(datetime(2026,9,11,16,20),99,99,[{'memory_percent':99}], 'offline'),
            ]);db.commit()
            result=history_payload(db,1,90,end)
        self.assertEqual(len(result['data']),2)
        self.assertEqual(result['data'][1]['time'],'2026-09-11T16:00:00Z')
        self.assertEqual(result['data'][1]['cpu'],80)
        self.assertEqual(result['data'][1]['gpu'],90)
        self.assertEqual(result['summary']['cpu']['average'],56.67)
        self.assertEqual(result['summary']['cpu']['peak'],100)
        self.assertEqual(result['summary']['gpu']['average'],60)
        self.assertEqual(result['samples'],4)

    def test_missing_values_do_not_reduce_daily_average(self):
        with self.Session() as db:
            db.add_all([self.row(self.now-timedelta(minutes=20),40,20),
                        self.row(self.now-timedelta(minutes=10),0,0,status='stale')]);db.commit()
            # SQLAlchemy's model default substitutes zero on INSERT, so explicitly set NULL.
            row=self.row(self.now-timedelta(minutes=5),0,30);db.add(row);db.commit()
            db.query(ServerStats).filter(ServerStats.id==row.id).update({'cpu_percent':None});db.commit()
            result=history_payload(db,1,90,self.now)
        self.assertEqual(result['summary']['cpu']['average'],40)
        self.assertEqual(result['summary']['ram']['average'],25)
        self.assertIsNone(result['summary']['gpu']['average'])

    def test_window_boundaries_and_future_rows(self):
        start=self.now-timedelta(days=1)
        with self.Session() as db:
            db.add_all([self.row(start-timedelta(seconds=1)),self.row(start,10),
                        self.row(self.now,20),self.row(self.now+timedelta(seconds=1),100)]);db.commit()
            result=history_payload(db,1,1,self.now)
        self.assertEqual(len(result['data']),2)
        self.assertEqual(result['summary']['cpu']['average'],15)

    def test_partial_first_daily_bucket_visible_and_all_missing(self):
        with self.Session() as db:
            db.add(self.row(self.now-timedelta(days=90)+timedelta(minutes=1), status='offline'));db.commit()
            result=history_payload(db,1,90,self.now)
        self.assertEqual(result['data'][0]['time'],result['start'])
        self.assertIsNone(result['data'][0]['cpu'])
        self.assertIsNone(result['summary']['cpu']['peak'])

    def test_no_records(self):
        with self.Session() as db: result=history_payload(db,1,365,self.now)
        self.assertEqual(result['data'],[])
        self.assertEqual(result['samples'],0)


del _Fixtures
if __name__=='__main__': unittest.main()
