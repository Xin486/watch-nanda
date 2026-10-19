"""Firewall regressions use simulated commands; never change the host firewall."""
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == 'linux', 'Node installer requires Linux')
class AgentNetworkTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2]/'agent/install_node.py'
        spec = importlib.util.spec_from_file_location('node_network_test', source)
        self.node = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.node)
        self.temp = tempfile.TemporaryDirectory()
        self.stage = Path(self.temp.name)
        self.chown = patch.object(self.node.os, 'chown')
        self.chown.start()

    def tearDown(self):
        self.chown.stop()
        self.temp.cleanup()

    def result(self, stdout='', returncode=0):
        return Mock(stdout=stdout, returncode=returncode)

    def test_inspection_checks_tls_without_adding_firewall_rules(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.24', 443))]
        with patch.object(self.node, 'firewall_state', return_value=['ufw']), \
             patch.object(self.node.socket, 'getaddrinfo', return_value=address), \
             patch.object(self.node.socket, 'create_connection') as connect, \
             patch.object(self.node.ssl, 'create_default_context') as tls, \
             patch.object(self.node, 'command') as command:
            result = self.node.check_network(self.stage, 'https://192.168.1.24/api/agent/report')
        self.assertTrue(result['tls_verified'])
        self.assertFalse(result['service_account_verified'])
        self.assertEqual(result['rules_added'], 0)
        command.assert_not_called()
        connect.assert_called_once_with(('192.168.1.24', 443), timeout=10)
        self.assertEqual(tls.return_value.wrap_socket.call_args.kwargs['server_hostname'], '192.168.1.24')
        self.assertFalse((self.stage/'network-changes.json').exists())

    def test_ufw_only_allows_exact_outbound_destination_and_rolls_back_own_rule(self):
        with patch.object(self.node, 'command', return_value=self.result()) as command:
            self.assertEqual(self.node.allow_report_egress(self.stage, ['ufw'], ['192.168.1.24'], 443), 1)
            journal = json.loads((self.stage/'network-changes.json').read_text())
            self.node.rollback_network(self.stage)
        added = command.call_args_list[1].args[0]
        self.assertEqual(added[:7], ['ufw', 'insert', '1', 'allow', 'out', 'proto', 'tcp'])
        self.assertEqual(added[7:11], ['to', '192.168.1.24', 'port', '443'])
        self.assertEqual(command.call_args_list[-1].args[0], journal[0])
        self.assertNotIn('disable', added)
        self.assertFalse((self.stage/'network-changes.json').exists())

    def test_matching_existing_ufw_rule_is_not_modified_or_removed(self):
        for address, destination in [('192.168.1.24','192.168.1.24'),('192.168.1.24','192.168.1.24/32'),
                                     ('2001:db8::1','2001:0db8:0000:0000:0000:0000:0000:0001/128')]:
            with self.subTest(destination=destination):
                existing = f'ufw allow out from any to {destination} port 443 proto tcp comment "admin existing"'
                with patch.object(self.node, 'command', return_value=self.result(existing)) as command:
                    self.assertEqual(self.node.allow_report_egress(self.stage, ['ufw'], [address], 443), 0)
                    self.node.rollback_network(self.stage)
                command.assert_called_once_with(['ufw', 'show', 'added'], timeout=15)

    def test_firewalld_runtime_and_persistent_rules_do_not_reload_or_open_input(self):
        def execute(args, **kwargs):
            return self.result(returncode=1 if '--query-rule' in args else 0)
        with patch.object(self.node, 'command', side_effect=execute) as command:
            self.assertEqual(self.node.allow_report_egress(self.stage, ['firewalld'], ['114.212.165.149'], 59443), 2)
            self.node.rollback_network(self.stage)
        creates = [c.args[0] for c in command.call_args_list if '--add-rule' in c.args[0]]
        self.assertEqual(len(creates), 2)
        self.assertTrue(any('--permanent' in c for c in creates))
        for args in creates:
            self.assertIn('OUTPUT', args)
            self.assertIn('114.212.165.149', args)
            self.assertIn('59443', args)
            self.assertNotIn('INPUT', args)
            self.assertNotIn('--reload', args)
        self.assertEqual(sum('--remove-rule' in c.args[0] for c in command.call_args_list), 2)

    def test_partial_firewalld_failure_removes_only_the_new_runtime_rule(self):
        def execute(args, **kwargs):
            if '--query-rule' in args:
                return self.result(returncode=1)
            if '--add-rule' in args and '--permanent' in args:
                raise RuntimeError('permanent write failed')
            return self.result()
        with patch.object(self.node, 'command', side_effect=execute) as command, self.assertRaises(RuntimeError):
            self.node.allow_report_egress(self.stage, ['firewalld'], ['192.168.1.24'], 443)
        deletes = [c.args[0] for c in command.call_args_list if '--remove-rule' in c.args[0]]
        self.assertEqual(len(deletes), 1)
        self.assertNotIn('--permanent', deletes[0])
        self.assertFalse((self.stage/'network-changes.json').exists())

    def test_certificate_failure_rolls_back_added_egress_without_disabling_tls(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.24', 443))]
        with patch.object(self.node, 'firewall_state', return_value=['ufw']), \
             patch.object(self.node.socket, 'getaddrinfo', return_value=address), \
             patch.object(self.node, 'command', return_value=self.result()) as command, \
             patch.object(self.node.ssl, 'create_default_context', side_effect=ssl.SSLCertVerificationError()), \
             self.assertRaisesRegex(RuntimeError, '证书校验失败'):
            self.node.check_network(self.stage, 'https://192.168.1.24/api/agent/report', configure=True)
        self.assertTrue(any(c.args[0][:4] == ['ufw', '--force', 'delete', 'allow'] for c in command.call_args_list))
        self.assertFalse((self.stage/'network-changes.json').exists())

    def test_conflicting_firewall_managers_are_not_changed(self):
        with patch.object(self.node, 'command') as command, self.assertRaisesRegex(RuntimeError, '多个防火墙'):
            self.node.allow_report_egress(self.stage, ['ufw', 'firewalld'], ['192.168.1.24'], 443)
        command.assert_not_called()

    def test_partial_rollback_records_progress_for_a_later_retry(self):
        removes=[['ufw','--force','delete','first'],['ufw','--force','delete','second']]
        journal=self.stage/'network-changes.json'
        journal.write_text(json.dumps(removes))
        def execute(args,**kwargs):
            return self.result(returncode=1 if args[-1]=='first' else 0)
        with patch.object(self.node,'command',side_effect=execute),self.assertRaises(RuntimeError):
            self.node.rollback_network(self.stage)
        self.assertEqual(json.loads(journal.read_text()),removes[:1])
        with patch.object(self.node,'command',return_value=self.result()) as command:
            self.node.rollback_network(self.stage)
        command.assert_called_once_with(removes[0],check=False,timeout=30)
        self.assertFalse(journal.exists())
