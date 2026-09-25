"""Moss-only integration; supply the source memory gate with isolated policy paths."""
import json
import time
from types import SimpleNamespace

import pytest


def test_signed_admission_binds_exact_wire_body_and_keeps_idempotency(monkeypatch):
    from agent import moss_memory_gate as gate
    from api import gateway_chat
    monkeypatch.setattr(gate, "_key", lambda: b"f" * 32)
    monkeypatch.setattr(gate, "policy", lambda: {"profiles": ["moss"]})
    monkeypatch.setattr(gate, "_seen", {})
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return b'{"run_id":"fixture-run"}'
    def open_request(request, **kwargs):
        captured.append(request)
        return Response()
    monkeypatch.setattr(gateway_chat, "_gateway_urlopen", open_request)
    admission = {"profile": "moss", "expires": time.time() + 60}
    body = {"session_id": "fixture-session", "profile": "moss", "input": "fixture"}
    headers = gateway_chat._gateway_run_headers("fixture-session", "fixture-key")
    assert gateway_chat._admit_gateway_run("https://unused.invalid/v1/runs", headers, body, "fixture-stream", memory_admission=admission) == "fixture-run"
    req = captured[-1]
    assert req.data == json.dumps(body).encode()
    assert req.get_header("Idempotency-key") == "webui-fixture-stream"
    proof = req.get_header("X-moss-memory-proof")
    assert proof
    assert gate.verify_request(proof, req.data + b" ", "fixture-session", "moss") is None
    assert gate.verify_request(proof, req.data, "fixture-session", "moss")["surface"] == "webui"
    assert gate.verify_request(proof, req.data, "fixture-session", "moss") is None
    # Restart replay has no persisted browser admission and must not mint proof.
    gateway_chat._admit_gateway_run("https://unused.invalid/v1/runs", headers, body, "fixture-stream")
    assert captured[-1].get_header("X-moss-memory-proof") is None
    assert captured[-1].get_header("Idempotency-key") == "webui-fixture-stream"
    assert "X-Moss-Memory-Proof" not in headers


@pytest.mark.parametrize("target,source,goal,allowed", [
    ("local_gateway", "webui", False, True),
    ("remote_gateway", "webui", False, False),
    ("local_direct", "webui", False, False),
    ("local_gateway", "service_session_launch", False, False),
    ("local_gateway", "webui", True, False),
    ("local_gateway", "cron", False, False),
])
def test_memory_admission_remains_browser_local_only(target, source, goal, allowed):
    from agent import moss_memory_gate as gate
    from api import routes
    admission = {"profile": "moss", "expires": time.time() + 60}
    token = gate.web_admission.set(admission)
    try:
        result = routes._moss_gateway_memory_admission({"execution_target": target}, source, goal)
        assert result == (admission if allowed else None)
    finally:
        gate.web_admission.reset(token)


def test_browser_capture_is_authenticated_and_clears_context(monkeypatch):
    from agent import moss_memory_gate as gate
    from api import auth, profiles, routes
    monkeypatch.setattr(gate, "policy", lambda: {"profiles": ["moss"], "password_owner": "Rodolfo"})
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "moss")
    monkeypatch.setattr(auth, "parse_cookie", lambda handler: "fixture-cookie")
    info = {"auth_type": "password", "expiry": time.time() + 60}
    monkeypatch.setattr(auth, "get_session_info", lambda cookie: info)
    wrapped = gate.capture_browser(lambda *args: gate.web_admission.get())
    assert wrapped(SimpleNamespace(), {})["profile"] == "moss"
    assert gate.web_admission.get() is None
    info["auth_type"] = "api_key"
    assert wrapped(SimpleNamespace(), {}) is None
    info.pop("auth_type")
    monkeypatch.setattr(routes, "j", lambda handler, body, status: (status, body))
    assert wrapped(SimpleNamespace(), {})[0] == 409
