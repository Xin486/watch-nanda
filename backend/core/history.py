"""History calculations: valid samples only, Beijing calendar days, compact SQL reads."""
import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from models.models import ServerStats

METRICS = ('cpu', 'ram', 'gpu')
LOCAL_OFFSET = timedelta(hours=8)


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 100 else None


def gpu_percent(data):
    values = [percent(g.get('memory_percent')) for g in data if isinstance(g, dict)] if isinstance(data, list) else []
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def mysql_rows(db, server_id, start, end, daily):
    # Extract only numeric VRAM percentages on the DB server. Full GPU JSON includes
    # process detail and is far larger than the three metrics the chart needs.
    gpu = """(SELECT AVG(CASE WHEN JSON_TYPE(j.pct) IN ('INTEGER', 'DOUBLE') THEN
          CASE WHEN CAST(j.pct AS DOUBLE) BETWEEN 0 AND 100 THEN CAST(j.pct AS DOUBLE) END END)
        FROM JSON_TABLE(CASE WHEN JSON_TYPE(s.gpu_data) = 'ARRAY' THEN s.gpu_data ELSE JSON_ARRAY() END,
          '$[*]' COLUMNS(pct JSON PATH '$.memory_percent' NULL ON EMPTY NULL ON ERROR)) j)"""
    source = f"""SELECT s.timestamp,
        CASE WHEN s.status = 'online' AND s.cpu_percent BETWEEN 0 AND 100 THEN s.cpu_percent END cpu,
        CASE WHEN s.status = 'online' AND s.ram_percent BETWEEN 0 AND 100 THEN s.ram_percent END ram,
        CASE WHEN s.status = 'online' THEN {gpu} END gpu
        FROM server_stats s WHERE s.server_id = :server_id
        AND s.timestamp >= :start AND s.timestamp <= :end"""
    if daily:
        aggregates = ', '.join(f'SUM({m}) {m}_sum, COUNT({m}) {m}_count, MAX({m}) {m}_peak' for m in METRICS)
        query = f"""SELECT DATE(timestamp + INTERVAL 8 HOUR) day, {aggregates}, COUNT(*) samples
            FROM ({source}) measurements GROUP BY day ORDER BY day"""
    else:
        query = source + ' ORDER BY s.timestamp, s.id'
    return db.execute(text(query), {'server_id': server_id, 'start': start, 'end': end}).mappings()


def history_payload(db, server_id, days, now=None):
    end = now or datetime.utcnow()
    start = end - timedelta(days=days)
    daily = days > 7
    totals = {m: {'sum': 0., 'count': 0, 'peak': None} for m in METRICS}
    points = []
    samples = 0

    def add_total(metric, total, count, peak):
        t = totals[metric]
        t['sum'] += float(total or 0)
        t['count'] += count
        if peak is not None:
            t['peak'] = max(t['peak'], float(peak)) if t['peak'] is not None else float(peak)

    def daily_point(day, metrics, count):
        # Clip a partial first day to the requested range, so its average is visible.
        stamp = max(datetime.combine(day, datetime.min.time()) - LOCAL_OFFSET, start)
        p = {'time': stamp.isoformat() + 'Z', 'samples': count}
        for m in METRICS:
            total, n, peak = metrics[m]
            p[m] = round(total / n, 2) if n else None
            add_total(m, total, n, peak)
        points.append(p)

    if db.bind.dialect.name == 'mysql':
        rows = mysql_rows(db, server_id, start, end, daily)
        if daily:
            for r in rows:
                samples += r['samples']
                daily_point(r['day'], {m: (float(r[m+'_sum'] or 0), r[m+'_count'], r[m+'_peak']) for m in METRICS}, r['samples'])
        else:
            for r in rows:
                samples += 1
                p = {'time': r['timestamp'].isoformat() + 'Z'}
                for m in METRICS:
                    v = percent(r[m])
                    p[m] = round(v, 2) if v is not None else None
                    add_total(m, v, int(v is not None), v)
                points.append(p)
    else:
        # Portable reference implementation, also used by isolated SQLite tests.
        rows = db.query(ServerStats.timestamp, ServerStats.status, ServerStats.cpu_percent,
                        ServerStats.ram_percent, ServerStats.gpu_data).filter(
            ServerStats.server_id == server_id, ServerStats.timestamp >= start,
            ServerStats.timestamp <= end).order_by(ServerStats.timestamp, ServerStats.id).yield_per(500)
        grouped = defaultdict(list)
        for r in rows:
            samples += 1
            values = dict(zip(METRICS, (percent(r.cpu_percent), percent(r.ram_percent), gpu_percent(r.gpu_data))))
            if r.status != 'online':
                values = dict.fromkeys(METRICS)
            if daily:
                grouped[(r.timestamp + LOCAL_OFFSET).date()].append(values)
            else:
                points.append({'time': r.timestamp.isoformat() + 'Z', **{m: round(v, 2) if v is not None else None for m, v in values.items()}})
                for m, v in values.items():
                    add_total(m, v, int(v is not None), v)
        for day, values in grouped.items():
            metrics = {}
            for m in METRICS:
                valid = [v[m] for v in values if v[m] is not None]
                metrics[m] = (sum(valid), len(valid), max(valid) if valid else None)
            daily_point(day, metrics, len(values))

    return {'server_id': server_id, 'data': points, 'start': start.isoformat()+'Z',
            'end': end.isoformat()+'Z', 'timezone': 'Asia/Shanghai',
            'aggregation': 'day' if daily else 'raw', 'samples': samples,
            'summary': {m: {'average': round(t['sum']/t['count'], 2) if t['count'] else None,
                            'peak': round(t['peak'], 2) if t['peak'] is not None else None,
                            'count': t['count']} for m, t in totals.items()}}
