"""Run manually from backend: python3 tests/check_history_mysql.py.

Uses a connection-local TEMPORARY table. Production history is never modified.
"""
import json
import sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.orm import Session
from models.database import engine
from core.history import history_payload


def main():
    with engine.connect() as conn:
        conn.execute(text('''CREATE TEMPORARY TABLE server_stats (
            id INT PRIMARY KEY, server_id INT, timestamp DATETIME, status VARCHAR(20),
            cpu_percent DOUBLE, ram_percent DOUBLE, gpu_data JSON)'''))
        try:
            rows = [
                (1,'2026-09-11 15:50:00','online',10,20,[{'memory_percent':30}]),
                (2,'2026-09-11 16:00:00','online',60,40,[]),
                (3,'2026-09-11 16:10:00','online',100,60,[{'memory_percent':90}, {}, {'memory_percent':'bad'}, {'memory_percent':True}]),
                (4,'2026-09-11 16:20:00','offline',99,99,[{'memory_percent':99}]),
                (5,'2026-09-11 16:30:00','online',None,None,{'invalid':True}),
                (6,'2026-09-11 16:40:00','stale',0,0,[]),
            ]
            for i,stamp,status,cpu,ram,gpu in rows:
                conn.execute(text('INSERT INTO server_stats VALUES (:id,1,:stamp,:status,:cpu,:ram,:gpu)'),
                             dict(id=i,stamp=stamp,status=status,cpu=cpu,ram=ram,gpu=json.dumps(gpu)))
            with Session(bind=conn) as db:
                daily=history_payload(db,1,90,datetime(2026,9,12,12))
                raw=history_payload(db,1,1,datetime(2026,9,12,12))
            assert daily['samples']==6 and len(daily['data'])==2
            assert daily['data'][1]['time']=='2026-09-11T16:00:00Z'
            assert daily['data'][1]['cpu']==80 and daily['data'][1]['gpu']==90
            assert daily['summary']==raw['summary'], (daily['summary'],raw['summary'])
            assert daily['summary']['cpu']['average']==56.67
            assert daily['summary']['gpu']['average']==60
            assert raw['data'][4]['cpu'] is None and raw['data'][4]['gpu'] is None
            assert raw['data'][5]['cpu'] is None
            print('PASS MySQL daily/raw parity, Beijing boundary, weighted averages, peaks, nulls, invalid JSON values')
        finally:
            conn.execute(text('DROP TEMPORARY TABLE server_stats'))

if __name__=='__main__': main()
