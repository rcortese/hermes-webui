"""Networkless contract tests for Agent-owned approval identities.

Run directly with python; no WebUI server or live agent is started.
"""
import ast
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract(path, name, namespace=None):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict[str, Any] = {} if namespace is None else namespace
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return ns[name]


translate = extract(ROOT / 'api/gateway_chat.py', '_gateway_runs_approval_event')
spec = importlib.util.spec_from_file_location('contract_runner', ROOT / 'api/runner_client.py')
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ApprovalIdentityContract(unittest.TestCase):
    def event(self, **ids):
        return translate(dict(tool='terminal', run_id='run-test', description='fixture only', **ids))

    def test_agent_request_id_reaches_card(self):
        self.assertEqual(self.event(request_id='request-second')['approval_id'], 'request-second')

    def test_backend_request_id_wins_over_legacy_card_id(self):
        self.assertEqual(self.event(request_id='request-second', approval_id='legacy-card')['approval_id'], 'request-second')

    def test_legacy_ids_preserved(self):
        self.assertEqual(self.event(approval_id='old')['approval_id'], 'old')
        self.assertEqual(self.event(id='older')['approval_id'], 'older')

    def test_identical_replay_is_stable(self):
        self.assertEqual(self.event(request_id='request-second'), self.event(request_id='request-second'))

    def test_transport_sends_exact_backend_identity(self):
        client = runner.HttpRunnerClient(base_url='http://unused.invalid')
        client._post = lambda path, body: (path, body)
        path, body = client.respond_approval('run-test', 'request-second', 'once')
        self.assertEqual(path, '/v1/runs/run-test/approval')
        self.assertEqual(body['request_id'], 'request-second')
        self.assertEqual(body['choice'], 'once')

    def test_real_agent_queue_once_deny_and_stale_id(self):
        agent_path = Path('/opt/hermes/tools/approval.py')
        if not agent_path.exists():
            self.skipTest('Run image compatibility test with the pinned Agent installed')
        queues = {}
        resolve = extract(agent_path, 'resolve_gateway_approval',
                          {'Optional': __import__('typing').Optional,
                           '_lock': threading.Lock(), '_gateway_queues': queues})
        for choice in ('once', 'deny'):
            a = SimpleNamespace(data={'request_id': 'first'}, event=threading.Event(), result=None)
            b = SimpleNamespace(data={'request_id': 'second'}, event=threading.Event(), result=None)
            queues['session'] = [a, b]
            client = runner.HttpRunnerClient(base_url='http://unused.invalid')
            # Same lookup key used by api_server_runs._handle_approve_run.
            client._post = lambda path, body: resolve('session', body['choice'], request_id=body.get('request_id'))
            self.assertEqual(client.respond_approval('run-test', self.event(request_id='second')['approval_id'], choice), 1)
            self.assertFalse(a.event.is_set(), 'Must not resolve FIFO head instead of clicked card')
            self.assertEqual(b.result, choice)
            self.assertTrue(b.event.is_set())
            self.assertEqual(client.respond_approval('run-test', 'second', choice), 0)
            self.assertFalse(a.event.is_set(), 'Replay must not approve a different pending request')
            self.assertEqual(client.respond_approval('run-test', 'unknown', choice), 0)
            self.assertFalse(a.event.is_set())


if __name__ == '__main__':
    unittest.main()
