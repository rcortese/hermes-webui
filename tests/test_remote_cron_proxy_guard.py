from pathlib import Path


CRON_GET_ENDPOINTS = [
    "/api/crons",
    "/api/crons/output",
    "/api/crons/history",
    "/api/crons/run",
    "/api/crons/recent",
    "/api/crons/status",
    "/api/crons/delivery-options",
]

CRON_POST_ENDPOINTS = [
    "/api/crons/create",
    "/api/crons/update",
    "/api/crons/delete",
    "/api/crons/run",
    "/api/crons/pause",
    "/api/crons/resume",
]


def test_remote_cron_proxy_payload_is_fail_closed_and_secret_safe():
    from api.routes import _remote_cron_proxy_unsupported_payload

    payload = _remote_cron_proxy_unsupported_payload({
        "name": "roy",
        "label": "Roy",
        "api_key": "must-not-leak",
        "base_url": "http://roy.invalid:8645",
    })

    assert payload == {
        "error": "remote_cron_proxy_unsupported",
        "profile_kind": "remote_gateway_proxy",
        "remote_proxy": True,
        "backend": "unsupported_remote",
        "profile": "roy",
        "label": "Roy",
        "message": (
            "Cron operations for remote profile proxies are not supported here. "
            "Moss-local cron jobs were not read or modified"
        ),
    }
    assert "must-not-leak" not in repr(payload)
    assert "roy.invalid" not in repr(payload)


def test_remote_cron_guard_uses_canonical_profile_proxy(monkeypatch):
    from api import config, profile_proxy, routes

    emitted = []
    monkeypatch.setattr(config, "get_config", lambda: {"marker": True})
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "roy")
    monkeypatch.setattr(
        profile_proxy,
        "profile_proxy_for",
        lambda name, cfg: {"name": name, "label": "Roy", "api_key": "secret"}
        if name == "roy" and cfg == {"marker": True}
        else None,
    )
    monkeypatch.setattr(routes, "j", lambda handler, payload: emitted.append((handler, payload)))

    handler = object()
    assert routes._guard_remote_cron_proxy(handler) is True
    assert emitted[0][0] is handler
    assert emitted[0][1]["error"] == "remote_cron_proxy_unsupported"
    assert "secret" not in repr(emitted)


def test_remote_cron_guard_fails_closed_when_target_classification_errors(monkeypatch):
    from api import config, profile_proxy, routes

    emitted = []
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "roy")
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(
        profile_proxy,
        "profile_proxy_for",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload: emitted.append(payload))

    assert routes._guard_remote_cron_proxy(object()) is True
    assert emitted == [routes._remote_cron_proxy_unsupported_payload({"name": "roy", "label": "roy"})]


def test_remote_cron_guard_allows_confirmed_local_profile(monkeypatch):
    from api import config, profile_proxy, routes

    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "moss")
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(profile_proxy, "profile_proxy_for", lambda *_args, **_kwargs: None)

    assert routes._guard_remote_cron_proxy(object()) is False


def test_all_cron_routes_guard_before_local_cron_context():
    source = (Path(__file__).parent.parent / "api" / "routes.py").read_text(encoding="utf-8")
    for endpoint in set(CRON_GET_ENDPOINTS + CRON_POST_ENDPOINTS):
        marker = f'if parsed.path == "{endpoint}":'
        positions = []
        start = 0
        while True:
            idx = source.find(marker, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + len(marker)
        assert positions, endpoint
        for idx in positions:
            next_route = source.find("\n    if parsed.path == ", idx + len(marker))
            block = source[idx : next_route if next_route >= 0 else len(source)]
            guard_idx = block.find("_guard_remote_cron_proxy(handler)")
            local_idx = block.find("cron_profile_context")
            assert guard_idx >= 0, endpoint
            assert local_idx == -1 or guard_idx < local_idx, endpoint
