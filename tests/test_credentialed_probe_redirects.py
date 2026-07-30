"""Regression coverage for credentialed gateway probes rejecting redirects."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from api import agent_health, config


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(*servers_and_threads):
    for server, _thread in servers_and_threads:
        server.shutdown()
        server.server_close()
    for _server, thread in servers_and_threads:
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    config.invalidate_gateway_caps()
    agent_health._reset_remote_probe_cache_for_tests()
    yield
    config.invalidate_gateway_caps()
    agent_health._reset_remote_probe_cache_for_tests()


def test_capabilities_probe_rejects_cross_origin_redirect_without_target_contact():
    """The bearer-authenticated capabilities probe must stop at a 302."""
    target_requests: list[dict[str, str | None]] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            target_requests.append({"authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"features": {"approval_events": True}}).encode())

        def log_message(self, format, *args):  # noqa: A002
            pass

    target, target_thread = _serve(Target)

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/v1/capabilities")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002
            pass

    redirector, redirector_thread = _serve(Redirector)
    try:
        caps = config.get_gateway_caps(f"http://127.0.0.1:{redirector.server_port}", "capability-secret")
    finally:
        _stop((redirector, redirector_thread), (target, target_thread))

    assert caps["capabilities_reachable"] is True
    assert caps["approval_events"] is False
    assert caps["probe_error"] and "HTTPError" in caps["probe_error"]
    assert target_requests == [], "redirect target must not be contacted"


def test_capabilities_probe_direct_request_retains_bearer_authorization():
    """The redirect guard must not strip Authorization from a direct probe."""
    received: list[str | None] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"features": {"approval_events": True, "run_approval_response": True}}).encode())

        def log_message(self, format, *args):  # noqa: A002
            pass

    gateway, gateway_thread = _serve(Gateway)
    try:
        caps = config.get_gateway_caps(f"http://127.0.0.1:{gateway.server_port}", "capability-secret")
    finally:
        _stop((gateway, gateway_thread))

    assert caps["capabilities_reachable"] is True
    assert caps["approval_events"] is True
    assert caps["run_approval_response"] is True
    assert received == ["Bearer capability-secret"]


def test_authenticated_health_probe_rejects_cross_origin_redirect_without_target_contact(monkeypatch):
    """An authenticated health redirect is a failed probe, never a target request."""
    target_requests: list[dict[str, str | None]] = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            target_requests.append({"authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"gateway_state":"running"}')

        def log_message(self, format, *args):  # noqa: A002
            pass

    target, target_thread = _serve(Target)

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/health/detailed")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002
            pass

    redirector, redirector_thread = _serve(Redirector)
    monkeypatch.setenv("HERMES_API_URL", f"http://127.0.0.1:{redirector.server_port}")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_API_KEY", "health-secret")
    try:
        payload = agent_health.build_agent_health_payload()
    finally:
        _stop((redirector, redirector_thread), (target, target_thread))

    assert payload["alive"] is False
    assert payload["details"]["reason"] == "remote_gateway_unreachable"
    assert target_requests == [], "redirect target must not be contacted"


def test_authenticated_health_probe_direct_request_retains_bearer_authorization(monkeypatch):
    """A direct detailed-health probe remains authenticated and alive."""
    received: list[str | None] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"gateway_state":"running"}')

        def log_message(self, format, *args):  # noqa: A002
            pass

    gateway, gateway_thread = _serve(Gateway)
    monkeypatch.setenv("HERMES_API_URL", f"http://127.0.0.1:{gateway.server_port}")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_API_KEY", "health-secret")
    try:
        payload = agent_health.build_agent_health_payload()
    finally:
        _stop((gateway, gateway_thread))

    assert payload["alive"] is True
    assert payload["details"]["gateway_state"] == "running"
    assert received == ["Bearer health-secret"]
