"""Focused release-port contracts for The AI Crowd WebUI overlay."""
from __future__ import annotations

from types import SimpleNamespace


def _remote_env() -> dict[str, str]:
    return {
        "HERMES_WEBUI_PROFILE_PROXY_ROY_BASE_URL": "http://roy.invalid:8645",
        "HERMES_WEBUI_PROFILE_PROXY_ROY_REMOTE_PROFILE": "roy-persona",
        "HERMES_WEBUI_PROFILE_PROXY_ROY_SESSION_KEY_PREFIX": "webui:roy",
    }


def test_execution_targets_are_canonical_and_remote_is_fail_closed():
    from api.gateway_chat import resolve_execution_target, webui_chat_backend_mode

    assert webui_chat_backend_mode({}, {}) == "legacy"
    assert webui_chat_backend_mode({}, {"HERMES_WEBUI_CHAT_BACKEND": "legacy-direct"}) == "legacy"
    remote = resolve_execution_target("ROY", local_gateway_enabled=False, environ={**_remote_env(), "HERMES_WEBUI_PROFILE_PROXY_ROY_API_KEY": "remote-test-key"}, profiles=[])
    assert remote["ok"] is True
    assert remote["execution_target"] == "remote_gateway"
    assert remote["gateway_config"]["remote_profile"] == "roy-persona"
    assert resolve_execution_target("missing", local_gateway_enabled=False, environ={}, profiles=[])["execution_target"] == "fail_closed"


def test_remote_proxy_without_credential_fails_closed():
    from api.gateway_chat import resolve_execution_target

    assert resolve_execution_target("roy", local_gateway_enabled=False, environ=_remote_env(), profiles=[]) == {
        "ok": False, "_status": 503, "error_type": "remote_profile_auth_unconfigured",
        "execution_target": "fail_closed", "error": "selected remote profile has no configured gateway credential",
    }


def test_remote_local_homonym_is_rejected_case_insensitively():
    from api.gateway_chat import resolve_execution_target

    result = resolve_execution_target("roy", local_gateway_enabled=False, environ={**_remote_env(), "HERMES_WEBUI_PROFILE_PROXY_ROY_API_KEY": "remote-test-key"}, profiles=[{"name": "Roy"}])
    assert result == {
        "ok": False, "_status": 409, "error_type": "ambiguous_profile",
        "execution_target": "fail_closed", "error": "selected profile is both local and remote",
    }


def test_remote_target_owns_runtime_before_local_revision_barrier(monkeypatch):
    """Global local-direct mode must not make a remote proxy hit the local barrier."""
    from api import gateway_chat, profiles, routes

    barrier_calls = []
    monkeypatch.setattr(routes, "get_config", lambda: {})
    monkeypatch.setattr(gateway_chat, "webui_gateway_chat_enabled", lambda _cfg: False)
    monkeypatch.setattr(gateway_chat, "_gateway_base_url", lambda _cfg: "http://local.invalid:8642")
    monkeypatch.setattr(gateway_chat, "_gateway_api_key", lambda: "local-key")
    monkeypatch.setattr(profiles, "list_profiles_api", lambda **_kwargs: [])
    monkeypatch.setattr(
        gateway_chat,
        "resolve_execution_target",
        lambda *_args, **_kwargs: {
            "ok": True,
            "execution_target": "remote_gateway",
            "profile_kind": "remote_gateway_proxy",
            "gateway_config": {"base_url": "http://roy.invalid:8645", "api_key": "remote-key"},
        },
    )
    monkeypatch.setattr(
        routes,
        "_agent_runtime_barrier_response",
        lambda **kwargs: barrier_calls.append(kwargs) or {"error": "stop after ownership check"},
    )

    result = routes._start_chat_stream_for_session(
        SimpleNamespace(profile="roy"),
        msg="hello",
        workspace="/tmp",
        model="provider/model",
        external_runtime_owned=False,
    )

    assert barrier_calls == [{"external_runtime_owned": True}]
    assert result == {"error": "stop after ownership check", "_status": 409}


def test_profile_selector_uses_canonical_order_and_preserves_active_state(monkeypatch):
    from api import config, profiles

    proxies = [
        {"name": "Zed", "remote_proxy": True, "is_active": False},
        {"name": "alpha", "remote_proxy": True, "is_active": False},
    ]
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr("api.profile_proxy.profile_proxy_public_entries", lambda _config: proxies)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "moss")

    rows = profiles._with_remote_profile_proxies([
        {"name": "zulu", "is_active": False},
        {"name": "default", "is_active": False},
        {"name": "moss", "is_active": False},
        {"name": "bravo", "is_active": False},
    ])

    assert [row["name"] for row in rows] == ["moss", "alpha", "Zed", "default", "bravo", "zulu"]
    assert [row["is_active"] for row in rows] == [True, False, False, False, False, False]


def test_profile_selector_suppresses_local_proxy_homonyms_case_insensitively(monkeypatch):
    from api import config, profiles

    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(
        "api.profile_proxy.profile_proxy_public_entries",
        lambda _config: [{"name": "RoY", "remote_proxy": True, "is_active": False}],
    )
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    rows = profiles._with_remote_profile_proxies([
        {"name": "roy", "is_active": True},
        {"name": "other", "is_active": False},
    ])

    assert [row["name"] for row in rows] == ["RoY", "other"]
    assert sum(row["name"].casefold() == "roy" for row in rows) == 1


def test_profile_selector_handles_missing_moss_and_default(monkeypatch):
    from api import config, profiles

    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr("api.profile_proxy.profile_proxy_public_entries", lambda _config: [])
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "gamma")

    rows = profiles._with_remote_profile_proxies([
        {"name": "Zulu", "is_active": False},
        {"name": "gamma", "is_active": False},
    ])

    assert [row["name"] for row in rows] == ["gamma", "Zulu"]
    assert [row["is_active"] for row in rows] == [True, False]


def test_public_proxy_selector_never_contains_secret_or_path():
    from api.profile_proxy import profile_proxy_public_entries

    env = {**_remote_env(), "HERMES_WEBUI_PROFILE_PROXY_ROY_API_KEY": "not-a-real-secret"}
    row = profile_proxy_public_entries({}, env)[0]
    assert row["backend"] == "remote_gateway"
    assert row["base_url_host_only"] == "roy.invalid:8645"
    assert "api_key" not in row
    assert "base_url" not in row


def test_proxy_urls_reject_userinfo_invalid_ports_and_non_http_schemes():
    from api.profile_proxy import profile_proxy_entries

    for value in ("ftp://roy.invalid", "http://user:pass@roy.invalid", "https://roy.invalid:99999", "http:///missing-host", "https://roy.invalid/?query=1"):
        assert profile_proxy_entries({}, {"HERMES_WEBUI_PROFILE_PROXY_ROY_BASE_URL": value}) == {}


def test_remote_health_basic_fallback_is_explicitly_degraded(monkeypatch):
    from api import agent_health

    agent_health._reset_remote_probe_cache_for_tests()
    monkeypatch.setattr(agent_health, "_http_probe", lambda url, timeout, api_key=None: (url.endswith("/health"), 200, None, b'{"status":"ok"}'))
    payload = agent_health._probe_remote_gateway("http://gateway.invalid", now=1.0)
    assert payload["alive"] is True
    assert payload["details"]["telemetry_state"] == "basic_fallback"
    assert payload["details"]["degraded"] is True


def test_health_bearer_is_unredirected(monkeypatch):
    from api import agent_health

    captured = {}
    class Response:
        status = 200
        def getcode(self): return 200
        def read(self, _limit): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    def urlopen(request, timeout):
        captured.update(request.unredirected_hdrs)
        return Response()
    monkeypatch.setattr(agent_health.urllib_request, "urlopen", urlopen)
    assert agent_health._http_probe("http://gateway.invalid/health/detailed", 1, api_key="test-token")[0]
    assert captured["Authorization"] == "Bearer test-token"


def test_terminal_checkpointed_user_does_not_become_no_response():
    from api import streaming

    previous = [{"role": "user", "content": "follow up"}]
    merged = [*previous, {"role": "assistant", "content": "done"}]
    assert streaming._turn_transcript_lacks_final_assistant_answer(merged, previous, "follow up", source="webui") is False


def test_service_launcher_is_fail_closed_and_has_no_generic_surface(monkeypatch):
    import api.service_session_launch as launch

    monkeypatch.setenv(launch.TOKEN_ENV, "x" * 32)
    handler = SimpleNamespace(headers={launch.HEADER: "x" * 32})
    assert launch._authorized(handler)
    assert launch._validate_body({"profile": "moss", "workspace": "/tmp", "initial_prompt": "hi", "url": "http://bad"}) == (None, "unsupported field")
    assert launch.is_service_launch_path("/api/internal/session-launch")
    assert not launch.is_service_launch_path("/api/chat/start")


def test_service_launch_auth_bypass_is_post_only_and_exact(monkeypatch):
    import server

    calls = []
    monkeypatch.setattr(server, "check_auth_or_close", lambda _handler, _parsed: calls.append(True) or True)

    def fake_route(_handler, _parsed):
        return True

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        handler = server.Handler.__new__(server.Handler)
        handler.command = method
        handler.path = "/api/internal/session-launch"
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 0)
        server.Handler._handle_write(handler, fake_route)
    handler = server.Handler.__new__(server.Handler)
    handler.command = "POST"
    handler.path = "/api/internal/session-launch/nearby"
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 0)
    server.Handler._handle_write(handler, fake_route)

    assert len(calls) == 4


def test_service_launch_csrf_exemption_is_post_only_and_exact():
    from api import routes

    assert routes._csrf_exempt_path("/api/internal/session-launch", "POST") is True
    for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE"):
        assert routes._csrf_exempt_path("/api/internal/session-launch", method) is False
    assert routes._csrf_exempt_path("/api/internal/session-launch/nearby", "POST") is False


def test_service_launcher_persists_then_verifies_the_exact_stream(monkeypatch):
    import api.config as config
    import api.models as models
    import api.routes as routes
    import api.service_session_launch as launch
    import api.workspace as workspace

    class Session:
        session_id = "session-1"
        active_stream_id = "stream-1"
        saved = False
        def save(self): self.saved = True

    session = Session()
    responses = []
    monkeypatch.setenv(launch.TOKEN_ENV, "x" * 32)
    monkeypatch.setattr(launch, "_profile_exists", lambda _profile: True)
    monkeypatch.setattr(workspace, "resolve_trusted_workspace", lambda value: value)
    monkeypatch.setattr(routes, "_session_model_state_from_request", lambda model, provider: (model, provider))
    monkeypatch.setattr(models, "new_session", lambda **_kwargs: session)
    monkeypatch.setattr(models, "get_session", lambda sid: session if sid == session.session_id else (_ for _ in ()).throw(KeyError(sid)))
    monkeypatch.setattr(routes, "start_session_turn", lambda *args, **kwargs: {"stream_id": "stream-1", "_status": 200})
    monkeypatch.setattr(config, "STREAMS", {"stream-1": object()})
    monkeypatch.setattr(launch, "j", lambda _handler, payload, status=200: responses.append((status, payload)))

    handler = SimpleNamespace(headers={launch.HEADER: "x" * 32})
    assert launch.handle_service_session_launch(handler, {"profile": "moss", "workspace": "/tmp/work", "model": "provider/model", "initial_prompt": "start"})
    assert session.saved is True
    assert responses == [(201, {"session_id": "session-1", "stream_id": "stream-1", "verified_state": {"session_id": "session-1", "stream_id": "stream-1", "active": True}, "profile": "moss", "workspace": "/tmp/work", "model": "provider/model", "reasoning": "profile_default"})]
