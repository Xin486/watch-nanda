"""Pinned Hermes gateway adapter, executed inside its own managed runtime.

Unlike an ordinary detached CLI, this transport explicitly interrupts and closes
its gateway session. The bridge also invokes its control-only cleanup path when
the generation process is cancelled or fails.
"""
import asyncio
from contextlib import suppress
import json
import os
import signal
import sys
import uuid


async def run(payload):
    from hermes_cli.gateway_client import connect_gateway
    from hermes_cli.gateway_chat import client_env_policy
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.stream_json import StreamJsonEmitter

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    async with connect_gateway() as client:
        if stopped.is_set():
            return 130
        if payload.get('session'):
            snapshot = await client.rpc('session.resume', session_id=payload['session'])
        else:
            description = await client.rpc('runtime.describe')
            parameters = description.get('session_create', {}).get('parameters', [])
            snapshot = await client.rpc('session.create', request_id=uuid.uuid4().hex,
                                        source='oneshot', cwd=os.getcwd(),
                                        **client_env_policy(parameters))
        if payload.get('action') == 'stop':
            await asyncio.wait_for(client.rpc('session.interrupt', session_id=snapshot['stored_session_id'],
                                               execution_generation=snapshot.get('execution_generation', 0)), 3)
            # Do not equate an accepted stop with completed execution. Wait for
            # the authority's receipts, then use its canonical viewer-close RPC.
            for pending in snapshot.get('pending', []):
                if not pending.get('admission_id'):
                    continue
                for _ in range(100):
                    receipt = await client.rpc('prompt.receipt', session_id=snapshot['stored_session_id'],
                                               admission_id=pending['admission_id'])
                    if receipt.get('status') == 'terminal':
                        break
                    if receipt.get('status') == 'unknown':
                        raise RuntimeError('Execution outcome is unknown')
                    await asyncio.sleep(.2)
                else:
                    raise RuntimeError('Execution has not settled')
            detached = await asyncio.wait_for(client.rpc('session.detach',
                session_id=snapshot['stored_session_id'], subscription_id=snapshot['subscription_id'],
                end_reason='web_chat_close'), 3)
            if not detached.get('detached'):
                raise RuntimeError('Session close not confirmed')
            print(json.dumps({'type': 'result', 'exit_code': 0, 'text': ''}), flush=True)
            return 0
        emitter = StreamJsonEmitter(session_id=snapshot['stored_session_id'])
        view = GatewayChatView(client, snapshot, quiet=True, emitter=emitter)
        view.unattended = False  # Preserve approvals. Never use the -z auto-approve path.
        turn = asyncio.create_task(view.run(payload['prompt'], oneshot=True))
        stop = asyncio.create_task(stopped.wait())
        try:
            await asyncio.wait({turn, stop}, return_when=asyncio.FIRST_COMPLETED)
            if stop.done() and not turn.done():
                try:
                    await asyncio.wait_for(client.rpc('session.interrupt', session_id=view.session_id,
                                                       execution_generation=view.generation), 2)
                finally:
                    turn.cancel()
                    await asyncio.gather(turn, return_exceptions=True)
                return 130
            return await turn
        finally:
            stop.cancel()
            turn.cancel()
            await asyncio.gather(stop, turn, return_exceptions=True)
            with suppress(Exception):
                await asyncio.wait_for(client.rpc('session.detach', session_id=view.session_id,
                    subscription_id=snapshot['subscription_id'], end_reason='web_chat_close'), 3)


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(run(json.load(sys.stdin))))
    except Exception:
        # Provider/transport exceptions can contain credential URLs.
        print(json.dumps({'type': 'result', 'exit_code': 1, 'text': ''}), flush=True)
        raise SystemExit(1)
