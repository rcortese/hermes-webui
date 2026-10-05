"""Offline regression ports for remote Runs cards; current mirror/relay API."""
import json
import threading
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from api import gateway_chat, route_approvals as approvals, routes


def card(run='run-a', request='request-a'):
    return dict(run_id=run, approval_id=request, command='consent',
                pattern_key='remote-only', _remote_run=True)


def poll(sid):
    with patch('api.routes.j', side_effect=lambda handler, payload, **kwargs: payload):
        return routes._handle_approval_pending(None, urlparse('/?session_id=' + sid))


@pytest.fixture(autouse=True)
def isolated_remote_state():
    yield
    with approvals._lock:
        for sid in list(approvals._pending):
            if sid.startswith('port-'):
                approvals._pending.pop(sid, None)
        for key in list(getattr(approvals, '_remote_approval_runs', {})):
            if key[0].startswith('port-'):
                approvals._remote_approval_runs.pop(key, None)


def test_remote_poll_replay_and_run_scoped_cleanup():
    sid = 'port-poll'
    with approvals.remote_approval_run(sid, 'run-a'):
        approvals.submit_gateway_pending_mirror(sid, card())
        approvals.submit_gateway_pending_mirror(sid, card())
        assert poll(sid)['pending_count'] == 1
        assert poll('port-other')['pending'] is None
        with approvals.remote_approval_run(sid, 'run-b'):
            approvals.submit_gateway_pending_mirror(sid, card('run-b', 'request-b'))
            assert poll(sid)['pending_count'] == 2
        assert poll(sid)['pending_count'] == 1
        approvals.resolve_remote_pending(sid, 'run-a', 'request-a')
        approvals.submit_gateway_pending_mirror(sid, card())
        assert poll(sid)['pending'] is None
        with approvals._lock:
            approvals._pending[sid] = [dict(approval_id='local', command='local')]
    assert poll(sid)['pending']['approval_id'] == 'local'


@pytest.mark.parametrize('choice', ['once', 'deny', 'session', 'always'])
@pytest.mark.parametrize('approval_id', ['', 'request-a'])
@pytest.mark.parametrize('single_dict', [False, True])
def test_legacy_boundary_never_consumes_remote_card(choice, approval_id, single_dict):
    sid = 'port-legacy'
    approvals.submit_gateway_pending_mirror(sid, card())
    with approvals._lock:
        original = approvals._pending[sid][0]
        if single_dict:
            approvals._pending[sid] = original
    with patch('api.routes.approve_session') as grant, \
         patch('api.routes.approve_permanent') as permanent, \
         patch('api.routes.save_permanent_allowlist') as save:
        assert routes._resolve_approval_legacy(sid, approval_id, choice) is False
    grant.assert_not_called()
    permanent.assert_not_called()
    save.assert_not_called()
    assert poll(sid)['pending']['approval_id'] == 'request-a'


def test_legacy_keeps_current_local_exact_id_preference():
    sid = 'port-local-preference'
    approvals.submit_gateway_pending_mirror(sid, card())
    with approvals._lock:
        approvals._pending[sid].append(dict(approval_id='request-a', command='local', pattern_key='local-only'))
    with patch('api.routes.approve_session') as grant:
        assert routes._resolve_approval_legacy(sid, 'request-a', 'session') is True
    grant.assert_called_once_with(sid, 'local-only')
    assert poll(sid)['pending']['_remote_run'] is True


@pytest.mark.parametrize('result,status,remaining', [
    ({'ok': True}, 200, 0), ({'accepted': True}, 200, 0), ({'resolved': 1}, 200, 0),
    ({'ok': False}, 502, 1), ({'accepted': False}, 502, 1), ({}, 502, 1),
    ({'resolved': 0}, 502, 1), ({'resolved': True}, 502, 1),
])
def test_remote_acknowledgment_never_grants_locally(result, status, remaining):
    sid = 'port-relay'
    with approvals.remote_approval_run(sid, 'run-a'):
        approvals.submit_gateway_pending_mirror(sid, card())
        mirror = approvals.gateway_pending_mirror(sid, approval_id='request-a')
        with patch('api.gateway_chat.gateway_run_endpoint', return_value=('http://fake', '')), \
             patch('api.runner_client.HttpRunnerClient.respond_approval', return_value=result) as relay, \
             patch('api.routes.approve_session') as grant, \
             patch('api.routes.approve_permanent') as permanent, \
             patch('api.routes.save_permanent_allowlist') as save, \
             patch('api.routes._resolve_approval_legacy') as legacy:
            payload, actual_status = routes._relay_gateway_run_approval(sid, mirror, 'always', enable_yolo=False)
        assert actual_status == status
        assert payload['ok'] is (status == 200)
        relay.assert_called_once_with('run-a', '', 'always')  # current request-id compatibility
        legacy.assert_not_called()
        grant.assert_not_called()
        permanent.assert_not_called()
        save.assert_not_called()
        approvals.submit_gateway_pending_mirror(sid, card())
        assert poll(sid)['pending_count'] == remaining


@pytest.mark.parametrize('terminal', ['run.completed', 'run.cancelled', 'run.failed', 'disconnect', 'cancel', 'eof'])
def test_real_fresh_stream_lifecycle_and_responded_replay(terminal):
    sid = 'port-stream'
    cancel = threading.Event()
    events = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            request = dict(event='approval.request', request_id='request-a', command='consent')
            yield ('data: ' + json.dumps(request)).encode()
            assert poll(sid)['pending_count'] == 1
            yield ('data: ' + json.dumps(request)).encode()
            assert poll(sid)['pending_count'] == 1
            yield b'data: {"event":"approval.responded","request_id":"request-a"}'
            assert poll(sid)['pending'] is None
            yield ('data: ' + json.dumps(request)).encode()
            assert poll(sid)['pending'] is None
            yield b'data: {"event":"approval.request","request_id":"request-b","command":"second"}'
            assert poll(sid)['pending_count'] == 1
            approvals.submit_gateway_pending_mirror(sid, card('other-run', 'other-request'))
            if terminal == 'disconnect':
                raise RuntimeError('connection lost')
            if terminal == 'cancel':
                cancel.set()
            if terminal != 'eof':
                yield ('data: ' + json.dumps(dict(event=terminal, output='done'))).encode()

    def publish(event, data):
        if event == 'approval':
            assert poll(sid)['pending']['approval_id'] == data['approval_id']
        events.append(event)

    try:
        with patch('api.gateway_chat._admit_gateway_run', return_value='run-a'), \
             patch('api.gateway_chat._gateway_resolve_context_length', return_value=0), \
             patch('api.gateway_chat._open_gateway_run_events', return_value=Response()), \
             patch('api.config.gateway_supports_approval_identity_v1', return_value=False), \
             patch('api.gateway_chat._gateway_session_yolo_enabled', return_value=False):
            try:
                gateway_chat._run_gateway_runs_api_streaming(
                    sid, 'hi', 'test', '.', 'port-stream-id', 'http://fake', '', [], {},
                    put_gateway_event=publish, cancel_event=cancel,
                )
            except RuntimeError:
                assert terminal in ('disconnect', 'run.failed')
        assert events.count('approval') == 2
        assert poll(sid)['pending_count'] == (2 if terminal == 'eof' else 1)
        assert approvals.gateway_pending_mirror(sid, approval_id='other-request')['run_id'] == 'other-run'
    finally:
        gateway_chat._STREAM_RUN_IDS.pop('port-stream-id', None)
        gateway_chat._STREAM_RUN_LIFECYCLE.pop('port-stream-id', None)
