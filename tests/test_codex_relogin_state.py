"""Offline deliberate-login recovery and pre-network prior-state gates."""
import json
import time

import pytest

import api.oauth as oauth
from tests.test_shared_codex_auth import COOLDOWN, jwt, row, seed


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_AUTH_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(oauth, "_get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_invalidate_provider_state_caches", lambda *a: None)
    monkeypatch.setattr(oauth.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    oauth._OAUTH_FLOWS.clear()
    yield
    oauth._OAUTH_FLOWS.clear()


def grant(sub="rcortese", generation=2, account="shared-workspace"):
    return {"access_token": jwt(sub=sub, generation=generation, account=account),
            "refresh_token": sub + "-fresh-" + str(generation)}


@pytest.mark.parametrize("reason", ["token_invalidated", "token_revoked", "invalid_token",
                                      "invalid_grant", "unauthorized_client", "refresh_token_reused"])
def test_dead_relogin_real_core_selection(tmp_path, monkeypatch, reason):
    from agent import credential_pool as core
    from hermes_cli import auth
    path = tmp_path / "auth.json"
    before = seed(path, [row("rcortese", 0, last_status="dead", last_status_at=time.time(),
                            last_error_code=401, last_error_reason=reason,
                            last_error_message="terminal synthetic", last_error_reset_at=9999999999),
                         row("viviane", 1, **COOLDOWN)])
    monkeypatch.setattr(core, "_seed_from_env", lambda *a: (False, set()))
    monkeypatch.setattr(core, "_codex_access_token_is_expiring", lambda *a: False)
    monkeypatch.setattr(auth, "_probe_codex_quota_restored", lambda *a, **k: False)
    assert core.load_pool("openai-codex").select() is None
    oauth._persist_codex_credentials(tmp_path, grant())
    after = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert after[1] == before["credential_pool"]["openai-codex"][1]
    assert after[0]["last_status"] == "ok"
    for key in ("last_status_at", "last_error_code", "last_error_reason", "last_error_message", "last_error_reset_at"):
        assert after[0].get(key) is None
    for key in ("id", "label", "priority", "created_at"):
        assert after[0][key] == before["credential_pool"]["openai-codex"][0][key]
    selected = core.load_pool("openai-codex").select()
    assert selected.id == "rcortese-id"
    assert selected.access_token == grant()["access_token"]
    assert selected.refresh_token == grant()["refresh_token"]


@pytest.mark.parametrize("status,code,reason", [
    ("exhausted", 429, "quota"), ("exhausted", "429", "quota"),
    ("exhausted", 503, "upstream"), ("dead", 429, "token_invalidated"),
    ("dead", 500, "unrelated"),
])
def test_non_auth_quarantine_preserved(tmp_path, status, code, reason):
    path = tmp_path / "auth.json"
    state = dict(COOLDOWN, last_status=status, last_error_code=code, last_error_reason=reason)
    seed(path, [row("rcortese", 0, **state)])
    oauth._persist_codex_credentials(tmp_path, grant())
    after = json.loads(path.read_text())["credential_pool"]["openai-codex"][0]
    assert all(after[key] == value for key, value in state.items())


@pytest.mark.parametrize("mutation", ["access", "refresh", "remove", "id", "principal"])
def test_changed_matching_prior_state_rejected_without_write(tmp_path, mutation):
    path = tmp_path / "auth.json"
    seed(path)
    expected = oauth._capture_codex_login_state(tmp_path)
    store = json.loads(path.read_text())
    entry = store["credential_pool"]["openai-codex"][0]
    if mutation == "access": entry["access_token"] = jwt(generation=3)
    if mutation == "refresh": entry["refresh_token"] = "concurrent-refresh"
    if mutation == "remove": store["credential_pool"]["openai-codex"].pop(0)
    if mutation == "id": entry["id"] = "replacement-id"
    if mutation == "principal": entry["access_token"] = jwt(sub="other")
    path.write_text(json.dumps(store))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        oauth._persist_codex_credentials(tmp_path, grant(), expected_state=expected)
    assert path.read_bytes() == before


def test_expected_absent_conflict_and_unrelated_peer_change(tmp_path):
    path = tmp_path / "auth.json"
    seed(path, [row("rcortese", 0)])
    expected = oauth._capture_codex_login_state(tmp_path)
    oauth._persist_codex_credentials(tmp_path, grant())  # unrelated to new viviane
    oauth._persist_codex_credentials(tmp_path, grant("viviane"), expected_state=expected)
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        oauth._persist_codex_credentials(tmp_path, grant("viviane", 3), expected_state=expected)
    assert path.read_bytes() == before


def test_same_subject_different_account_is_new_exact_principal(tmp_path):
    path = tmp_path / "auth.json"
    seed(path, [row("rcortese", 0)])
    expected = oauth._capture_codex_login_state(tmp_path)
    before = json.loads(path.read_text())["credential_pool"]["openai-codex"][0]
    oauth._persist_codex_credentials(tmp_path, grant(account="other-account"), expected_state=expected)
    rows = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert rows[0] == before and len(rows) == 2


@pytest.mark.parametrize("change", ["rotate", "appear", "disappear"])
def test_singleton_prior_pair_or_absence_checked(tmp_path, change):
    path = tmp_path / "auth.json"
    store = seed(path)
    if change != "appear":
        store["providers"]["openai-codex"] = {"tokens": grant(generation=1)}
    path.write_text(json.dumps(store))
    expected = oauth._capture_codex_login_state(tmp_path)
    if change == "disappear": del store["providers"]["openai-codex"]
    else: store["providers"]["openai-codex"] = {"tokens": grant(generation=3)}
    path.write_text(json.dumps(store))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        oauth._persist_codex_credentials(tmp_path, grant(), expected_state=expected)
    assert path.read_bytes() == before


def test_auth_root_change_rejected(tmp_path, monkeypatch):
    seed(tmp_path / "auth.json")
    expected = oauth._capture_codex_login_state(tmp_path)
    monkeypatch.setenv("HERMES_AUTH_HOME", str(tmp_path / "different"))
    with pytest.raises(RuntimeError, match="changed"):
        oauth._persist_codex_credentials(tmp_path, grant(), expected_state=expected)
    assert not (tmp_path / "different" / "auth.json").exists()


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("boundary", ["device", "exchange"])
@pytest.mark.parametrize("entrypoint", ["onboarding", "legacy-start"])
def test_real_start_worker_snapshot_before_network_and_terminal_scrub(
        tmp_path, monkeypatch, concurrent, boundary, entrypoint):
    path = tmp_path / "auth.json"
    seed(path)
    def request():
        if concurrent and boundary == "device":
            oauth._persist_codex_credentials(tmp_path, grant(generation=3))
        return {"device_auth_id": "synthetic-device", "user_code": "ABCD", "interval": 3}
    def exchange(*args):
        if concurrent and boundary == "exchange":
            oauth._persist_codex_credentials(tmp_path, grant(generation=3))
        return grant()
    monkeypatch.setattr(oauth, "_request_codex_user_code", request)
    monkeypatch.setattr(oauth, "_spawn_codex_oauth_worker", lambda *a: None)
    monkeypatch.setattr(oauth.time, "sleep", lambda *a: None)
    monkeypatch.setattr(oauth, "_poll_codex_authorization", lambda *a: {"authorization_code": "a", "code_verifier": "v"})
    monkeypatch.setattr(oauth, "_exchange_codex_authorization", exchange)
    payload = (oauth.start_codex_device_code() if entrypoint == "legacy-start"
               else oauth.start_onboarding_oauth_flow({"provider": "openai-codex"}))
    assert "login_state" not in json.dumps(payload)
    fid = payload["flow_id"]
    before = path.read_bytes()
    oauth._run_codex_oauth_worker(fid)
    flow = oauth._OAUTH_FLOWS[fid]
    assert flow["status"] == ("error" if concurrent else "success")
    assert "login_state" not in flow
    if concurrent:
        assert json.loads(path.read_text())["credential_pool"]["openai-codex"][0]["refresh_token"] == grant(generation=3)["refresh_token"]
        if boundary == "device": assert path.read_bytes() == before
    else:
        assert json.loads(path.read_text())["credential_pool"]["openai-codex"][0]["refresh_token"] == grant()["refresh_token"]


@pytest.mark.parametrize("reason,code", [(None, 401), ("token_revoked", None)])
def test_dead_auth_evidence_without_both_markers(tmp_path, reason, code):
    path = tmp_path / "auth.json"
    seed(path, [row("rcortese", 0, last_status="dead", last_error_code=code,
                    last_error_reason=reason)])
    oauth._persist_codex_credentials(tmp_path, grant())
    assert json.loads(path.read_text())["credential_pool"]["openai-codex"][0]["last_status"] == "ok"


def test_metadata_and_peer_changes_preserved_with_snapshot(tmp_path):
    path = tmp_path / "auth.json"
    store = seed(path)
    expected = oauth._capture_codex_login_state(tmp_path)
    target, peer = store["credential_pool"]["openai-codex"]
    target.update(COOLDOWN, label="operator-new-label", priority=7)
    peer.update(access_token=jwt(sub="viviane", generation=3), refresh_token="peer-new",
                last_status="dead", last_error_code=401, last_error_reason="token_invalidated")
    path.write_text(json.dumps(store))
    oauth._persist_codex_credentials(tmp_path, grant(), expected_state=expected)
    rows = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert rows[1] == peer
    assert rows[0]["label"] == "operator-new-label" and rows[0]["priority"] == 7
    assert all(rows[0][k] == v for k, v in COOLDOWN.items())


@pytest.mark.parametrize("terminal", ["cancelled", "expired", "error", "success"])
def test_snapshot_scrubbed_on_all_terminal_states(terminal):
    flow = {"login_state": {"synthetic": "private-prior-token"}, "refresh_token": "synthetic"}
    oauth._OAUTH_FLOWS["scrub"] = flow
    oauth._set_flow_status("scrub", terminal)
    assert "login_state" not in flow and "refresh_token" not in flow


def test_operational_persistence_callsite_inventory():
    """Fail when a new production caller bypasses the pre-network CAS review."""
    import ast
    from pathlib import Path
    root = Path(oauth.__file__).resolve().parents[1]
    callers = []
    for path in sorted((root / "api").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = (call.func.id if isinstance(call.func, ast.Name)
                        else call.func.attr if isinstance(call.func, ast.Attribute) else "")
                if name in {"_persist_codex_credentials", "_save_codex_credentials"}:
                    callers.append((str(path.relative_to(root)), node.name, name))
                    if node.name == "_run_codex_oauth_worker":
                        assert any(k.arg == "expected_state" for k in call.keywords)
    assert sorted(callers) == [
        ("api/oauth.py", "_run_codex_oauth_worker", "_persist_codex_credentials"),
        ("api/oauth.py", "_save_codex_credentials", "_persist_codex_credentials"),
    ]


def test_six_profile_flows_share_two_rows_and_one_lock_root(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from hermes_cli import auth
    shared = tmp_path / "shared"
    path = shared / "auth.json"
    seed(path)
    monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    original_lock = auth._auth_store_lock
    locks = []
    @contextmanager
    def observed_lock(*args, **kwargs):
        locks.append(kwargs["target_path"])
        with original_lock(*args, **kwargs):
            yield
    monkeypatch.setattr(auth, "_auth_store_lock", observed_lock)
    monkeypatch.setattr(oauth, "_request_codex_user_code", lambda: {
        "device_auth_id": "synthetic", "user_code": "CODE", "interval": 3})
    monkeypatch.setattr(oauth, "_spawn_codex_oauth_worker", lambda *a: None)
    monkeypatch.setattr(oauth.time, "sleep", lambda *a: None)
    monkeypatch.setattr(oauth, "_poll_codex_authorization", lambda *a: {
        "authorization_code": "synthetic", "code_verifier": "synthetic"})
    monkeypatch.setattr(oauth, "_exchange_codex_authorization", lambda *a: grant())
    homes = [tmp_path / f"persona-{n}" for n in range(6)]
    flows = []
    for home in homes:
        monkeypatch.setattr(oauth, "_get_active_hermes_home", lambda home=home: home)
        flows.append(oauth.start_onboarding_oauth_flow({"provider": "openai-codex"})["flow_id"])
    oauth._run_codex_oauth_worker(flows[0])
    after_first = path.read_bytes()
    for fid in flows[1:]:
        oauth._run_codex_oauth_worker(fid)
        assert oauth._OAUTH_FLOWS[fid]["status"] == "error"
        assert path.read_bytes() == after_first
    assert oauth._OAUTH_FLOWS[flows[0]]["status"] == "success"
    store = json.loads(path.read_text())
    assert [r["id"] for r in store["credential_pool"]["openai-codex"]] == ["rcortese-id", "viviane-id"]
    assert "openai-codex" not in store["providers"]
    assert locks and set(locks) == {path}
    assert all(not (home / "auth.json").exists() for home in homes)
