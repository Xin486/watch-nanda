"""Optional loopback-only Hermes bridge. Run separately from the monitor worker.

The existing monitor process remains the authority for operator cookies. No model
key, node credential, raw tool output or arbitrary Hermes session ID crosses this API.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

PREFIX = '/api/ops/hermes'
COOKIE = 'watch_nanda_ops'
CLI = os.getenv('HERMES_CLI', '/data2/hermes-cli/hermes')
WORKSPACE = os.getenv('HERMES_CHAT_WORKSPACE', '/data2/hermes-cli/workspace')
MAX_OUTPUT = 128_000
TURN_TIMEOUT = max(60, min(3600, int(os.getenv('HERMES_CHAT_TURN_TIMEOUT_SECONDS', '1800'))))


def resolve_command():
    # Use Hermes' machine boundary so runtime repins do not leave us pointing at
    # an obsolete venv. The inert stdlib module finishes before our worker starts.
    result = subprocess.run([CLI, '--print-runtime-command', '--module', 'types'],
                            capture_output=True, timeout=15, check=True, text=True)
    command = json.loads(result.stdout)
    if not isinstance(command, list) or len(command) != 4 or command[1:3] != ['-I', '-c']:
        raise RuntimeError('Hermes 运行时接口已变化，请检查接入版本')
    command[3] += '; runpy.run_path(' + repr(str(Path(__file__).with_name('hermes_worker.py'))) + ", run_name='__main__')"
    return command


def verify_session(headers):
    """Ask the live monitor; never duplicate its in-memory session database."""
    connection = http.client.HTTPConnection('127.0.0.1', 7980, timeout=4)
    try:
        connection.request('GET', '/api/ops/session', headers=headers)
        response = connection.getresponse()
        response.read(4096)
        if response.status != 200:
            raise HTTPException(401 if response.status == 401 else 503,
                                '请重新进行运维登录' if response.status == 401 else '运维认证服务暂不可用')
    except (OSError, http.client.HTTPException):
        raise HTTPException(503, '运维认证服务暂不可用') from None
    finally:
        connection.close()


async def authenticate(request):
    host = request.headers.get('host', '')
    origin = request.headers.get('origin')
    if request.url.scheme != 'https' or (origin and origin != 'https://' + host):
        raise HTTPException(403, '聊天仅允许同源 HTTPS 访问')
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 256:
        raise HTTPException(401, '请先进行运维登录')
    headers = {'Host': host, 'X-Forwarded-Proto': 'https', 'Cookie': COOKIE + '=' + token}
    await asyncio.to_thread(verify_session, headers)
    return hashlib.sha256(token.encode()).hexdigest(), headers


@dataclass
class Conversation:
    owner: str
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    messages: list = field(default_factory=list)
    status: str = 'idle'
    error: str = ''
    activity: str = ''
    session: str = ''
    requests: set = field(default_factory=set)
    touched: float = field(default_factory=time.monotonic)
    task: object = None
    process: object = None
    command: object = None
    cancel_reason: str = ''

    def snapshot(self):
        self.touched = time.monotonic()
        cleaning = self.status != 'running' and self.task and not self.task.done()
        return {'id': self.id, 'messages': self.messages, 'status': 'running' if cleaning else self.status,
                'error': '' if cleaning else self.error,
                'activity': '正在确认任务已停止…' if cleaning else self.activity}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=16, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


class Bridge:
    def __init__(self):
        self.conversations = {}

    def create(self, owner):
        now = time.monotonic()
        for key, item in list(self.conversations.items()):
            if item.status != 'running' and now - item.touched > 3600:
                del self.conversations[key]
        if len(self.conversations) >= 50:
            raise HTTPException(429, '聊天会话已满，请稍后重试')
        item = Conversation(owner)
        self.conversations[item.id] = item
        return item

    def get(self, ident, owner):
        item = self.conversations.get(ident)
        if item is None or item.owner != owner:
            raise HTTPException(404, '对话已结束，请新建对话')
        return item

    def start(self, item, message, headers):
        if message.request_id in item.requests:
            return item.snapshot()
        if not message.text.strip():
            raise HTTPException(422, '请输入消息')
        running = [c for c in self.conversations.values() if c.status == 'running' or (c.task and not c.task.done())]
        if item.status == 'running' or any(c.owner == item.owner for c in running):
            raise HTTPException(409, '当前仍有回复正在生成，请等待或停止')
        if len(running) >= 2:
            raise HTTPException(429, 'Hermes 正忙，请稍后重试')
        if len(item.messages) >= 100:
            raise HTTPException(409, '对话过长，请新建对话')
        item.requests.add(message.request_id)
        item.messages.extend([{'role': 'user', 'text': message.text.strip()},
                              {'role': 'assistant', 'text': ''}])
        item.status, item.error, item.activity, item.cancel_reason = 'running', '', '正在连接 Hermes…', ''
        item.task = asyncio.create_task(self.run(item, message.text.strip(), headers))
        return item.snapshot()

    async def terminate(self, item):
        process = item.process
        if process is None:
            return
        # Always kill the dedicated group: tools may outlive their CLI parent.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        if item.session and item.command and item.status in ('stopped', 'error'):
            cleanup = None
            try:
                cleanup = await asyncio.create_subprocess_exec(*item.command, cwd=WORKSPACE,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                await asyncio.wait_for(cleanup.communicate(json.dumps(
                    {'action': 'stop', 'session': item.session}).encode()), 35)
                if cleanup.returncode:
                    raise RuntimeError('cleanup refused')
            except Exception:
                if cleanup and cleanup.returncode is None:
                    os.killpg(cleanup.pid, signal.SIGKILL)
                    await cleanup.wait()
                item.status, item.error = 'error', '停止结果尚未确认，请在 Hermes CLI 中检查该任务'

    async def stop(self, item, reason='已停止生成'):
        item.cancel_reason = reason
        if item.task and not item.task.done():
            item.task.cancel()
            await asyncio.gather(item.task, return_exceptions=True)
        # Includes cancellation before the task got its first event-loop turn.
        if item.status == 'running':
            item.status, item.error, item.activity = 'stopped', reason, ''

    async def read(self, item, prompt):
        Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
        command = await asyncio.to_thread(resolve_command)
        item.command = command
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *command, cwd=WORKSPACE, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True, limit=1_048_576))
        try:
            item.process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Cancellation can race the OS spawn. Recover its handle so the
            # outer finally always terminates the entire child process group.
            item.process = await spawning
            raise
        item.process.stdin.write(json.dumps({'prompt': prompt, 'session': item.session}).encode())
        await item.process.stdin.drain()
        item.process.stdin.close()
        result = None
        async for line in item.process.stdout:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            kind = event.get('type')
            if kind == 'system':
                session = str(event.get('session_id', ''))
                if re.fullmatch(r'[a-zA-Z0-9_-]{8,160}', session):
                    item.session = session
            elif kind == 'text':
                item.messages[-1]['text'] += str(event.get('text', ''))
                item.activity = '正在回复…'
            elif kind == 'tool_use':
                name = re.sub(r'[^a-zA-Z0-9_.-]', '', str(event.get('name', 'tool')))[:80]
                item.activity = '正在使用工具：' + name
            elif kind == 'result':
                result = event
                session = str(event.get('session_id', ''))
                if re.fullmatch(r'[a-zA-Z0-9_-]{8,160}', session):
                    item.session = session
                if event.get('text'):
                    item.messages[-1]['text'] = str(event['text'])
            if len(item.messages[-1]['text']) > MAX_OUTPUT:
                item.messages[-1]['text'] = item.messages[-1]['text'][:MAX_OUTPUT]
                raise RuntimeError('回复超过长度限制，请缩小问题范围')
        code = await item.process.wait()
        if not result or code or result.get('exit_code'):
            # Upstream errors may include credentials, tool output or URLs.
            raise RuntimeError('Hermes 未能完成回复，请稍后重试或新建对话')
        item.status = 'idle'

    async def run(self, item, prompt, headers):
        reader = asyncio.create_task(self.read(item, prompt))
        start = time.monotonic()
        try:
            while not reader.done():
                await asyncio.wait({reader}, timeout=2)
                if time.monotonic() - start > TURN_TIMEOUT:
                    raise RuntimeError('回复超时，请缩小问题范围后重试')
                if not reader.done():
                    await asyncio.to_thread(verify_session, headers)
            await reader
        except asyncio.CancelledError:
            item.status, item.error = 'stopped', item.cancel_reason or '已停止生成'
        except HTTPException:
            item.status, item.error = 'stopped', '运维会话已失效，请重新登录'
        except Exception as error:
            item.status = 'error'
            item.error = str(error) if isinstance(error, RuntimeError) else 'Hermes 暂不可用，请稍后重试'
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await self.terminate(item)
            item.process, item.activity = None, ''


bridge = Bridge()


@asynccontextmanager
async def lifespan(app):
    yield
    await asyncio.gather(*(bridge.stop(c, '服务正在重启') for c in bridge.conversations.values()))


app = FastAPI(title='Hermes Chat Bridge', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware('http')
async def private_response(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post(PREFIX + '/conversations')
async def create(request: Request):
    owner, _ = await authenticate(request)
    return bridge.create(owner).snapshot()


@app.get(PREFIX + '/conversations/{ident}')
async def snapshot(ident: str, request: Request):
    owner, _ = await authenticate(request)
    return bridge.get(ident, owner).snapshot()


@app.post(PREFIX + '/conversations/{ident}/messages')
async def send(ident: str, message: Message, request: Request):
    owner, headers = await authenticate(request)
    return bridge.start(bridge.get(ident, owner), message, headers)


@app.post(PREFIX + '/conversations/{ident}/stop')
async def stop(ident: str, request: Request):
    owner, _ = await authenticate(request)
    item = bridge.get(ident, owner)
    await bridge.stop(item)
    return item.snapshot()


@app.delete(PREFIX + '/conversations/{ident}')
async def delete(ident: str, request: Request):
    owner, _ = await authenticate(request)
    item = bridge.get(ident, owner)
    await bridge.stop(item)
    del bridge.conversations[ident]
    return {'deleted': True}
