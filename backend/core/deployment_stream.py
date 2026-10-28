"""Read-only, authenticated deployment feeds; bounded snapshots, no replayed commands."""
import asyncio
import json
import time

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from core import ops_auth

INTERVAL = 0.5
HEARTBEAT = 10


def frame(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


async def events(request, snapshot, finished):
    previous = None
    sent_at = 0
    while not await request.is_disconnected():
        try:
            # Cookies are checked throughout the stream, including logout/expiry.
            ops_auth.authorized(request)
            data = await asyncio.to_thread(snapshot)
        except HTTPException as error:
            yield frame('feed-error', {'status': error.status_code, 'detail': error.detail})
            return
        encoded = frame('snapshot', data)
        if encoded != previous:
            yield encoded
            previous, sent_at = encoded, time.monotonic()
        elif time.monotonic() - sent_at >= HEARTBEAT:
            yield frame('heartbeat', {})
            sent_at = time.monotonic()
        if finished(data):
            return
        # Coalesce bursts of installer output. No unbounded subscriber queues,
        # database queries, SSH access or task creation occur in this loop.
        await asyncio.sleep(INTERVAL)


async def response(request, snapshot, finished):
    # Validate existence before sending HTTP headers so REST-style errors work.
    await asyncio.to_thread(snapshot)
    return StreamingResponse(events(request, snapshot, finished), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
