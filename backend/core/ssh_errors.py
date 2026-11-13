"""Classify password authentication failures before closing their transport."""
import paramiko
from paramiko.ssh_exception import BadAuthenticationType, PartialAuthentication


AUTH_MESSAGES = {
    'authentication_timeout': 'SSH 认证超时，下一周期自动重试',
    'authentication_interrupted': 'SSH 认证期间连接中断，下一周期自动重试',
    'authentication_unavailable': 'SSH 认证过程暂时异常，下一周期自动重试',
    'authentication_unsupported': 'SSH 密码认证方式不可用，请检查节点认证配置',
    'authentication_rejected': 'SSH 服务器明确拒绝认证，请核对账号、密码后重新验证',
}


def authentication_failure_kind(error, client):
    """Only a rejection on a live transport proves that a password was refused.

    Paramiko also raises AuthenticationException on timeout, and synthesizes
    'Authentication failed.' when the transport disappears without a reason.
    Unknown failures remain retryable. Never expose the original error text.
    """
    if isinstance(error, (BadAuthenticationType, PartialAuthentication)):
        return 'authentication_unsupported'
    message = str(error).strip().lower()
    if message.startswith('authentication timeout'):
        return 'authentication_timeout'
    transport = client.get_transport() if client is not None else None
    if transport is None or not transport.is_active():
        return 'authentication_interrupted'
    if message == 'authentication failed.':
        return 'authentication_rejected'
    return 'authentication_unavailable'


class AuthenticationUnavailable(paramiko.SSHException):
    """A transient/method failure must not trigger another password attempt."""

    def __init__(self, kind):
        self.kind = kind
        super().__init__(AUTH_MESSAGES[kind])
