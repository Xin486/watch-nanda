"""Credential confidentiality, transport restrictions and password-only authentication."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import test_monitor
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request
from core.ssh_credentials import encrypt_password, decrypt_password
from core.status import server_payload
from models.models import Server
from worker import tasks
import main


class CredentialTests(unittest.TestCase):
    def setUp(self):
        test_monitor.MonitorTests.setUp(self)
        self.key = Path(self.temp.name) / 'credentials.key'
        self.key.write_bytes(Fernet.generate_key())
        self.key.chmod(0o600)
        self.env = patch.dict(os.environ, {'SSH_CREDENTIAL_KEY_FILE': str(self.key)})
        self.env.start()
        tasks.rejected_credentials.clear()

    def tearDown(self):
        self.env.stop()
        test_monitor.MonitorTests.tearDown(self)

    def test_ciphertext_roundtrip_tamper_and_connection_binding(self):
        server = SimpleNamespace(ip_address='127.0.0.1',hostname='test',ssh_user='test',ssh_port=22)
        server.ssh_password_encrypted = encrypt_password('private-test-password',server)
        self.assertNotIn('private-test-password',server.ssh_password_encrypted)
        self.assertEqual(decrypt_password(server),'private-test-password')
        server.ssh_user='other'
        with self.assertRaises(ValueError): decrypt_password(server)
        server.ssh_user='test'
        server.ssh_password_encrypted='broken'
        with self.assertRaises(ValueError): decrypt_password(server)

    def test_password_only_known_hosts_and_auth_failure_circuit(self):
        import paramiko
        server=SimpleNamespace(id=1,ip_address='127.0.0.1',hostname='test',ssh_user='test',ssh_port=22)
        server.ssh_password_encrypted=encrypt_password('private-test-password',server)
        with patch.object(tasks.paramiko,'SSHClient') as factory:
            client=factory.return_value
            client.get_transport.return_value.is_active.return_value=True
            client.connect.side_effect=paramiko.AuthenticationException('Authentication failed.')
            self.assertFalse(tasks.collect_server_data(server)[1])
            blocked,ok,_=tasks.collect_server_data(server)
            self.assertFalse(ok)
            self.assertEqual(blocked['ssh_error_code'],'authentication_rejected')
            self.assertFalse(blocked['ssh_retryable'])
            self.assertEqual(client.connect.call_count,1)
            client.load_system_host_keys.assert_called_once()
            self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0],paramiko.RejectPolicy)
            self.assertFalse(client.connect.call_args.kwargs['look_for_keys'])
            self.assertFalse(client.connect.call_args.kwargs['allow_agent'])
            server.ssh_password_encrypted=encrypt_password('new-password',server)
            tasks.collect_server_data(server)
            self.assertEqual(client.connect.call_count,2)

    def test_transient_authentication_failures_reconnect_on_the_next_collection(self):
        import paramiko
        cases = [
            ('Authentication timeout.', True, 'authentication_timeout'),
            ('Authentication failed.', False, 'authentication_interrupted'),
            ('Authentication failed.', None, 'authentication_interrupted'),
            ('sensitive-private-error', True, 'authentication_unavailable'),
        ]
        server = SimpleNamespace(id=1, ip_address='127.0.0.1', hostname='test', ssh_user='test', ssh_port=22)
        server.ssh_password_encrypted = encrypt_password('private-test-password', server)
        for message, active, expected in cases:
            with self.subTest(expected=expected, active=active), patch.object(tasks.paramiko, 'SSHClient') as factory:
                tasks.rejected_credentials.clear()
                client = factory.return_value
                if active is None:
                    client.get_transport.return_value = None
                else:
                    client.get_transport.return_value.is_active.return_value = active
                client.connect.side_effect = [paramiko.AuthenticationException(message), None]
                output = MagicMock()
                output.read.return_value = b'<<<UPTIME>>>\n123.45 45.67\n<<<END>>>'
                client.exec_command.return_value = (None, output, None)
                data, ok, _ = tasks.collect_server_data(server)
                self.assertFalse(ok)
                self.assertEqual(data['ssh_error_code'], expected)
                self.assertTrue(data['ssh_retryable'])
                self.assertNotIn('sensitive-private-error', str(data))
                self.assertNotIn(server.id, tasks.rejected_credentials)
                data, ok, _ = tasks.collect_server_data(server)
                self.assertTrue(ok)
                self.assertEqual(client.connect.call_count, 2)
                self.assertEqual(data['uptime_seconds'], 123)

    def test_unsupported_password_authentication_does_not_block_credentials(self):
        from paramiko.ssh_exception import BadAuthenticationType, PartialAuthentication
        server = SimpleNamespace(id=1, ip_address='127.0.0.1', hostname='test', ssh_user='test', ssh_port=22)
        server.ssh_password_encrypted = encrypt_password('private-test-password', server)
        errors = [BadAuthenticationType('private-message', ['publickey']), PartialAuthentication(['keyboard-interactive'])]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(tasks.paramiko, 'SSHClient') as factory:
                factory.return_value.connect.side_effect = error
                data, ok, _ = tasks.collect_server_data(server)
                self.assertFalse(ok)
                self.assertEqual(data['ssh_error_code'], 'authentication_unsupported')
                self.assertNotIn('private-message', str(data))
                self.assertNotIn(server.id, tasks.rejected_credentials)

    def test_missing_password_never_tries_keys(self):
        with self.Session() as db, patch.object(tasks.paramiko,'SSHClient') as factory:
            self.assertFalse(tasks.collect_server_data(db.get(Server,1))[1])
            factory.assert_not_called()

    def test_http_password_is_rejected_https_is_encrypted_and_not_returned(self):
        data=main.ServerUpdate(hostname='test',ip_address='127.0.0.1',group_name='test',ssh_user='root',ssh_password='private-test-password')
        with self.Session() as db:
            with self.assertRaises(HTTPException):
                main.update_server(1,data,db,Request({'type':'http','scheme':'http','path':'/','headers':[]}))
            db.rollback()
            main.update_server(1,data,db,Request({'type':'http','scheme':'https','path':'/','headers':[]}))
            node=db.get(Server,1)
            self.assertEqual(decrypt_password(node),'private-test-password')
            result=server_payload(node)
            self.assertTrue(result['has_ssh_password'])
            self.assertNotIn('ssh_password_encrypted',result)
            self.assertNotIn('private-test-password',str(result))
            main.update_server(1,main.ServerUpdate(hostname='test',ip_address='127.0.0.2',group_name='test'),db)
            self.assertIsNone(db.get(Server,1).ssh_password_encrypted)
