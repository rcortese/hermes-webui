"""Corrective regressions for the v0.52.76 AI Crowd port."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_credentialed_gateway_request_rejects_redirect_without_leaking_credentials(monkeypatch):
    """A redirect target must never receive gateway Authorization/session identity."""
    from api import gateway_chat

    received = {}

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            received.update({key.lower(): value for key, value in self.headers.items()})
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_args):
            pass

    target, _ = _serve(Target)

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.end_headers()
        def log_message(self, *_args):
            pass

    redirector, _ = _serve(Redirector)
    try:
        request = gateway_chat.urllib.request.Request(
            f"http://127.0.0.1:{redirector.server_port}/v1/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer test-secret", "X-Hermes-Session-Key": "webui:session-1"},
            method="POST",
        )
        try:
            gateway_chat._gateway_urlopen(request, timeout=1)
        except HTTPError as exc:
            assert exc.code == 302
        else:
            raise AssertionError("credentialed gateway redirect must fail closed")
        assert received == {}
    finally:
        redirector.shutdown()
        target.shutdown()


def test_credentialed_gateway_direct_request_carries_expected_credentials():
    """The redirect guard must not remove credentials from a direct gateway request."""
    from api import gateway_chat

    received = {}

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            received.update({key.lower(): value for key, value in self.headers.items()})
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_args):
            pass

    server, _ = _serve(Gateway)
    try:
        request = gateway_chat.urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=b"{}",
            headers={"Authorization": "Bearer test-secret", "X-Hermes-Session-Key": "webui:session-1"},
            method="POST",
        )
        with gateway_chat._gateway_urlopen(request, timeout=1):
            pass
        assert received["authorization"] == "Bearer test-secret"
        assert received["x-hermes-session-key"] == "webui:session-1"
    finally:
        server.shutdown()


def test_authenticated_remote_selector_switch_binds_remote_identity_and_new_turn_routes_remote(monkeypatch):
    """Remote selector never resolves a local home and routes the new session remotely."""
    from api import routes
    from api import profiles

    responses = []
    handler = SimpleNamespace(headers={}, _trusted_auth_session_cookie_value="signed-session")
    body = {"name": "ROY"}
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **kwargs: responses.append((status, payload, kwargs)))
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status=400: responses.append((status, {"error": message}, {})))
    monkeypatch.setattr("api.auth.ensure_trusted_auth_session", lambda _handler: {"bound_profile": None})
    monkeypatch.setattr("api.helpers.build_profile_cookie", lambda name, **_kwargs: f"profile={name}")
    monkeypatch.setattr("api.config.invalidate_models_cache", lambda **_kwargs: None)
    monkeypatch.setattr("api.gateway_watcher.restart_watcher_for_profile", lambda _name: None)
    monkeypatch.setattr(profiles, "remote_profile_selector", lambda name: {"name": "roy", "active": "roy", "default_model": "remote-model", "default_workspace": "/safe/workspace"} if name.casefold() == "roy" else None)
    monkeypatch.setattr("api.profile_proxy.profile_proxy_for", lambda name, *_args: {"name": "roy", "api_key_configured": True, "api_key": "remote-test-key", "base_url": "http://remote.invalid:8645", "remote_profile": "roy-persona"} if name.casefold() == "roy" else None)
    monkeypatch.setattr(profiles, "switch_profile", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote selector must not resolve a local profile home")))

    monkeypatch.setattr(routes, "read_body", lambda _handler: body)
    routes.handle_post(handler, SimpleNamespace(path="/api/profile/switch", query=""))
    assert responses[0][0] == 200
    assert responses[0][1]["active"] == "roy"
    assert responses[0][2]["extra_headers"]["Set-Cookie"] == "profile=roy"

    from api.profile_proxy import resolve_execution_target
    env = {
        "HERMES_WEBUI_PROFILE_PROXY_ROY_BASE_URL": "http://remote.invalid:8645",
        "HERMES_WEBUI_PROFILE_PROXY_ROY_API_KEY": "remote-test-key",
        "HERMES_WEBUI_PROFILE_PROXY_ROY_REMOTE_PROFILE": "roy-persona",
    }
    target = resolve_execution_target("roy", local_gateway_enabled=False, environ=env, profiles=[])
    assert target["execution_target"] == "remote_gateway"
    assert target["gateway_config"]["remote_profile"] == "roy-persona"


def test_remote_selector_collision_fails_closed_before_local_switch(monkeypatch):
    from api import profiles

    monkeypatch.setattr(profiles, "list_profiles_api", lambda **_kwargs: [{"name": "Roy", "remote_proxy": False}])
    monkeypatch.setattr("api.profile_proxy.profile_proxy_for", lambda _name: {"name": "roy", "api_key_configured": True})
    assert profiles.remote_profile_selector("roy") is None
