"""Process-isolated deployment-mode checks; never read real policy or keys."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


BLOCK_GATE = """
import sys
import importlib.abc
class MissingGate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'agent.moss_memory_gate':
            raise ModuleNotFoundError('fixture: memory gate unavailable', name=fullname)
sys.meta_path.insert(0, MissingGate())
"""


def run_isolated(tmp_path, mode, script):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "HERMES_HOME": str(tmp_path / "home"),
        "HERMES_BASE_HOME": str(tmp_path / "home"),
        "HERMES_CONFIG_PATH": str(tmp_path / "home/config.yaml"),
        "HERMES_WEBUI_STATE_DIR": str(tmp_path / "state"),
        "PYTHONPATH": os.pathsep.join(sys.path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if mode is not None:
        env["HERMES_WEBUI_MOSS_MEMORY_GATE"] = mode
    return subprocess.run(
        [sys.executable, "-c", BLOCK_GATE + textwrap.dedent(script)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=60,
    )


def test_disabled_imports_routes_and_sends_no_memory_identity(tmp_path):
    result = run_isolated(tmp_path, "disabled", """
        from api import routes, gateway_chat
        from api import memory_gate
        assert 'agent.moss_memory_gate' not in sys.modules
        assert routes._moss_gateway_memory_admission(
            {'execution_target': 'local_gateway'}, 'webui', False) is None
        called = []
        handler = object()
        body = {'message': 'ordinary Roy chat'}
        def original(h, b):
            called.append((h, b))
            return 'legacy-result'
        assert memory_gate.capture_browser(original)(handler, body) == 'legacy-result'
        assert called == [(handler, body)]
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return b'{"run_id":"fixture-run"}'
        def transport(req, **kwargs):
            captured.append(req)
            return Response()
        gateway_chat._gateway_urlopen = transport
        headers = gateway_chat._gateway_run_headers('session', 'synthetic-api-key')
        headers['x-MoSs-MeMoRy-PrOoF'] = 'stale-synthetic-proof'
        assert gateway_chat._admit_gateway_run(
            'https://unused.invalid/v1/runs', headers,
            {'session_id': 'session', 'profile': 'roy', 'input': 'ordinary chat'},
            'stream', memory_admission={'profile': 'moss', 'expires': 9999999999}
        ) == 'fixture-run'
        sent = dict((k.lower(), v) for k, v in captured[0].header_items())
        assert not any(k.startswith('x-moss-') for k in sent)
        assert sent['authorization'] == 'Bearer synthetic-api-key'
        assert sent['x-hermes-session-id'] == 'session'
        assert sent['idempotency-key'] == 'webui-stream'
        assert headers['x-MoSs-MeMoRy-PrOoF'] == 'stale-synthetic-proof'
        assert 'agent.moss_memory_gate' not in sys.modules
        print('disabled: legacy path and identity isolation verified')
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "identity isolation verified" in result.stdout


def test_enabled_signing_failure_never_submits_unsigned_run(monkeypatch):
    from api import gateway_chat, memory_gate

    sent = []

    def broken_signer(*args):
        raise ValueError("synthetic key custody failure")

    monkeypatch.setattr(memory_gate, "_MODE", "enabled")
    monkeypatch.setattr(memory_gate, "_sign_request", broken_signer, raising=False)
    monkeypatch.setattr(gateway_chat, "_gateway_urlopen", lambda *a, **kw: sent.append(a))
    with pytest.raises(ValueError, match="synthetic key custody failure"):
        gateway_chat._admit_gateway_run(
            "https://unused.invalid/v1/runs", {},
            {"session_id": "fixture-session"}, "fixture-stream",
            memory_admission={"profile": "moss"},
        )
    assert sent == []


@pytest.mark.parametrize("mode", [None, "enabled"])
def test_required_gate_missing_blocks_routes_import(tmp_path, mode):
    result = run_isolated(tmp_path, mode, "import api.routes")
    assert result.returncode != 0
    assert "memory gate unavailable" in result.stderr


@pytest.mark.parametrize("mode", ["", "false", "0", "disable", "auto"])
def test_unknown_mode_never_disables_gate(tmp_path, mode):
    result = run_isolated(tmp_path, mode, "import api.routes")
    assert result.returncode != 0
    assert "HERMES_WEBUI_MOSS_MEMORY_GATE must be enabled or disabled" in result.stderr
