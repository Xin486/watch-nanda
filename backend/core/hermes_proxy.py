"""Same-origin fallback for sites without a separate Nginx chat location."""
import asyncio
import http.client
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from core.operations import require_operator
from core.platform_config import public_hermes

router = APIRouter(prefix='/api/ops/hermes', dependencies=[Depends(require_operator)])


@router.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
async def proxy(path: str, request: Request):
    if not public_hermes()['enabled']:
        raise HTTPException(503, '助手已关闭，请在设置中开启')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, '请求过大')
    headers = {'Host': request.headers.get('host', ''), 'X-Forwarded-Proto': 'https',
               'Cookie': request.headers.get('cookie', ''), 'Content-Type': 'application/json'}
    if request.headers.get('origin'):
        headers['Origin'] = request.headers['origin']
    def forward():
        connection = http.client.HTTPConnection('127.0.0.1', int(os.getenv('HERMES_BRIDGE_PORT', '7981')), timeout=60)
        try:
            connection.request(request.method, '/api/ops/hermes/' + path, body=bytes(body), headers=headers)
            result = connection.getresponse()
            return result.status, result.read(16 * 1024 * 1024)
        finally:
            connection.close()
    try:
        status, payload = await asyncio.to_thread(forward)
        return Response(payload, status_code=status, media_type='application/json', headers={'Cache-Control': 'no-store'})
    except (OSError, http.client.HTTPException):
        raise HTTPException(503, '助手运行服务暂不可用，请检查安装状态') from None
