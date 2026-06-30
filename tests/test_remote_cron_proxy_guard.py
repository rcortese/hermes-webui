from pathlib import Path


CRON_GET_ENDPOINTS = [
    '/api/crons',
    '/api/crons/output',
    '/api/crons/history',
    '/api/crons/run',
    '/api/crons/recent',
    '/api/crons/status',
    '/api/crons/delivery-options',
]

CRON_POST_ENDPOINTS = [
    '/api/crons/create',
    '/api/crons/update',
    '/api/crons/delete',
    '/api/crons/run',
    '/api/crons/pause',
    '/api/crons/resume',
]


def _routes_source() -> str:
    test_path = Path(__file__)
    candidates = [
        test_path.with_name('routes.py'),
        test_path.parent.parent / 'api' / 'routes.py',
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding='utf-8')
    raise AssertionError(f'routes.py not found in candidates: {candidates}')


def test_remote_cron_proxy_payload_is_fail_closed_and_secret_safe():
    source = _routes_source()
    assert 'def _remote_cron_proxy_unsupported_payload' in source
    assert '"error": "remote_cron_proxy_unsupported"' in source
    assert '"profile_kind": "remote_gateway_proxy"' in source
    assert '"remote_proxy": True' in source
    assert '"backend": "unsupported_remote"' in source
    assert 'Moss-local cron jobs were not read or modified' in source
    payload_fn = source[source.find('def _remote_cron_proxy_unsupported_payload'):source.find('def _active_remote_cron_proxy')]
    assert 'api_key' not in payload_fn


def test_all_cron_routes_guard_before_local_cron_context():
    source = _routes_source()
    for endpoint in set(CRON_GET_ENDPOINTS + CRON_POST_ENDPOINTS):
        marker = f'if parsed.path == "{endpoint}":'
        idx = source.find(marker)
        assert idx != -1, endpoint
        block = source[idx:source.find('\n    if parsed.path == ', idx + 1)]
        assert '_guard_remote_cron_proxy(handler)' in block, endpoint
        guard_idx = block.find('_guard_remote_cron_proxy(handler)')
        local_idx = block.find('cron_profile_context')
        assert local_idx == -1 or guard_idx < local_idx, endpoint
