"""Synthetic offline regression gates for shared Codex auth WebUI paths."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import api.oauth as oauth

COOLDOWN = {
    "last_status": "exhausted", "last_status_at": "2026-09-30T00:00:00Z",
    "last_error_code": "429", "last_error_reason": "quota",
    "last_error_message": "synthetic cooldown", "last_error_reset_at": 9999999999,
}


def jwt(account="shared-workspace", sub="rcortese", generation=1):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account},
              "sub": sub, "generation": generation}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def row(sub, priority, **extra):
    return {"id": sub + "-id", "label": sub, "priority": priority,
            "source": "manual:device_code", "auth_type": "oauth",
            "created_at": "original", "access_token": jwt(sub=sub),
            "refresh_token": sub + "-refresh-1", **extra}


def seed(path, entries=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    store = {"version": 1, "credential_pool": {"openai-codex": entries if entries is not None else [
        row("rcortese", 0, **COOLDOWN), row("viviane", 1)]},
        "providers": {"other": {"api_key": "synthetic-other"}}, "sentinel": "keep"}
    path.write_text(json.dumps(store))
    return store


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_AUTH_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    monkeypatch.setattr(oauth, "_get_active_hermes_home", lambda: tmp_path / "profile")
    monkeypatch.setattr(oauth, "_invalidate_provider_state_caches", lambda provider: None)
    # Any unintended HTTP/network use is a test failure.
    monkeypatch.setattr(oauth.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network forbidden"))


def test_root_precedence_and_default(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    assert oauth.get_shared_auth_path(home) == home / "auth.json"
    monkeypatch.setenv("HERMES_AUTH_HOME", "   ")
    assert oauth.get_shared_auth_path(home) == home / "auth.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_AUTH_HOME", "  ~/shared  ")
    assert oauth.get_shared_auth_path(home) == tmp_path / "shared" / "auth.json"


@pytest.mark.parametrize("shared_override", [False, True])
def test_shared_second_principal_relogin_preserves_pool_and_cooldowns(tmp_path, monkeypatch, shared_override):
    shared = tmp_path / "shared"
    if shared_override:
        monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    auth_path = shared / "auth.json"
    before = seed(auth_path)
    home = tmp_path / "profile" if shared_override else shared
    result = oauth._persist_codex_credentials(home, {
        "access_token": jwt(sub="viviane", generation=2), "refresh_token": "viviane-refresh-2"})
    after = json.loads(auth_path.read_text())
    entries = after["credential_pool"]["openai-codex"]
    assert result == auth_path
    assert len(entries) == 2
    assert entries[0] == before["credential_pool"]["openai-codex"][0]
    for key in ("id", "label", "priority", "source", "created_at"):
        assert entries[1][key] == before["credential_pool"]["openai-codex"][1][key]
    assert entries[1]["access_token"] == jwt(sub="viviane", generation=2)
    assert entries[1]["refresh_token"] == "viviane-refresh-2"
    assert after["providers"] == before["providers"]
    assert "openai-codex" not in after["providers"]
    assert not (tmp_path / "profile" / "auth.json").exists()
    assert auth_path.stat().st_mode & 0o777 == 0o600
    assert shared.stat().st_mode & 0o777 == 0o700


def test_authenticated_and_config_and_selfheal_use_shared_store(tmp_path, monkeypatch):
    from api.onboarding import _provider_oauth_authenticated
    from api import config, profiles
    shared = tmp_path / "shared"
    monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    home = tmp_path / "profile"
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: home)
    store = seed(shared / "auth.json")
    assert _provider_oauth_authenticated("openai-codex", home)
    assert config._get_auth_store_path() == shared / "auth.json"
    assert config._credential_pool_profile_tag() == str(shared / "auth.json")
    assert oauth.read_auth_json() == store
    # A misleading local store must not mask an absent shared store.
    (shared / "auth.json").unlink()
    seed(home / "auth.json")
    assert not _provider_oauth_authenticated("openai-codex", home)
    assert oauth.read_auth_json() == {}


def test_streaming_selfheal_reaches_resolver_with_only_shared_auth(tmp_path, monkeypatch):
    from api import streaming, config
    from hermes_cli import runtime_provider
    shared = tmp_path / "shared"
    monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    seed(shared / "auth.json")
    evicted = []
    resolved = []
    monkeypatch.setattr(config, "SESSION_AGENT_CACHE", {})
    monkeypatch.setattr(config, "invalidate_credential_pool_cache", lambda provider: evicted.append(provider))
    def resolve(**kwargs):
        resolved.append(kwargs)
        return {"provider": "openai-codex", "api_key": "synthetic"}
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", resolve)
    result = streaming._attempt_credential_self_heal("openai-codex", "synthetic-session", None,
                                                    target_model="synthetic-model")
    assert result == {"provider": "openai-codex", "api_key": "synthetic"}
    assert evicted == ["openai-codex"]
    assert resolved == [{"requested": "openai-codex", "target_model": "synthetic-model"}]


def test_no_override_paths_preserve_active_profile(tmp_path, monkeypatch):
    from api import config, profiles
    from api.onboarding import _provider_oauth_authenticated
    home = tmp_path / "profile"
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: home)
    store = seed(home / "auth.json")
    assert config._get_auth_store_path() == home / "auth.json"
    assert oauth.read_auth_json() == store
    assert _provider_oauth_authenticated("openai-codex", home)


@pytest.mark.parametrize("raw", ['{broken', '[]', '{"credential_pool":null}',
    '{"credential_pool":[]}', '{"credential_pool":{"openai-codex":null}}',
    '{"credential_pool":{"openai-codex":{}}}'])
def test_corrupt_store_never_cleared(tmp_path, raw):
    path = tmp_path / "auth.json"
    path.write_text(raw)
    with pytest.raises(RuntimeError):
        oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(), "refresh_token": "new"})
    assert path.read_text() == raw


@pytest.mark.parametrize("entries", [
    [row("rcortese", 0), row("rcortese", 1)],
    [row("rcortese", 0), {"access_token": "opaque"}],
    [None],
])
def test_unidentified_or_duplicate_existing_principal_rejected(tmp_path, entries):
    path = tmp_path / "auth.json"
    seed(path, entries)
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(), "refresh_token": "new"})
    assert path.read_bytes() == before


def test_opaque_empty_local_compatibility_and_shared_rejection(tmp_path, monkeypatch):
    local = tmp_path / "local"
    path = oauth._persist_codex_credentials(local, {"access_token": "opaque", "refresh_token": "synthetic"})
    assert json.loads(path.read_text())["credential_pool"]["openai-codex"][0]["access_token"] == "opaque"
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        oauth._persist_codex_credentials(local, {"access_token": "opaque2", "refresh_token": "synthetic2"})
    assert path.read_bytes() == before
    shared = tmp_path / "shared"
    monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    with pytest.raises(RuntimeError):
        oauth._persist_codex_credentials(local, {"access_token": "opaque", "refresh_token": "synthetic"})
    assert not (shared / "auth.json").exists()


def test_new_principal_appends_without_displacing_fill_first(tmp_path):
    path = tmp_path / "auth.json"
    seed(path, [row("rcortese", 0)])
    oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(sub="viviane"), "refresh_token": "v"})
    entries = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert [e["priority"] for e in entries] == [0, 1]
    assert [e["label"] for e in entries] == ["rcortese", "Codex OAuth"]


def test_core_lock_blocks_login_and_login_rereads_after_lock(tmp_path):
    path = tmp_path / "auth.json"
    seed(path)
    # The other process holds the actual core auth.lock, then rotates rcortese.
    script = '''import json, sys
from pathlib import Path
from hermes_cli.auth import _auth_store_lock, _save_auth_store
p = Path(sys.argv[1])
with _auth_store_lock(target_path=p):
    print("LOCKED", flush=True)
    assert sys.stdin.readline().strip() == "GO"
    store = json.loads(p.read_text())
    store["credential_pool"]["openai-codex"][0]["refresh_token"] = "concurrent-latest"
    store["concurrent_metadata"] = "keep"
    _save_auth_store(store, target_path=p)
'''
    proc = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    errors = []
    reached_lock = threading.Event()
    real_lock = __import__("hermes_cli.auth", fromlist=["_auth_store_lock"])._auth_store_lock
    from contextlib import contextmanager
    @contextmanager
    def observed_lock(*args, **kwargs):
        reached_lock.set()
        with real_lock(*args, **kwargs):
            yield
    from unittest.mock import patch
    def login():
        try:
            oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(sub="viviane", generation=2),
                                                        "refresh_token": "v-latest"})
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=login)
    try:
        assert proc.stdout.readline().strip() == "LOCKED"
        with patch("hermes_cli.auth._auth_store_lock", observed_lock):
            thread.start()
            assert reached_lock.wait(5)
            thread.join(0.25)
            assert thread.is_alive(), "login bypassed core lock"
            assert json.loads(path.read_text())["credential_pool"]["openai-codex"][1]["refresh_token"] == "viviane-refresh-1"
            proc.stdin.write("GO\n")
            proc.stdin.flush()
            thread.join(10)
            assert not thread.is_alive()
        assert proc.wait(10) == 0, proc.stderr.read()
        assert not errors
        store = json.loads(path.read_text())
        assert store["concurrent_metadata"] == "keep"
        assert store["credential_pool"]["openai-codex"][0]["refresh_token"] == "concurrent-latest"
        assert store["credential_pool"]["openai-codex"][1]["refresh_token"] == "v-latest"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
        thread.join(10) if thread.ident else None


def test_two_process_logins_no_clobber(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    path = shared / "auth.json"
    before = seed(path)
    script = '''import sys
from pathlib import Path
from api.oauth import _persist_codex_credentials
_persist_codex_credentials(Path(sys.argv[1]), {"access_token": sys.argv[2], "refresh_token": sys.argv[3]})
'''
    env = dict(os.environ, HERMES_AUTH_HOME=str(shared))
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path / sub),
                                   jwt(sub=sub, generation=2), sub + "-latest"],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for sub in ("rcortese", "viviane")]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
        store = json.loads(path.read_text())
        entries = store["credential_pool"]["openai-codex"]
        assert len(entries) == 2
        assert [e["id"] for e in entries] == ["rcortese-id", "viviane-id"]
        assert [e["refresh_token"] for e in entries] == ["rcortese-latest", "viviane-latest"]
        assert all(entries[0][key] == value for key, value in COOLDOWN.items())
        assert store["providers"] == before["providers"]
        assert not (tmp_path / "rcortese" / "auth.json").exists()
        assert not (tmp_path / "viviane" / "auth.json").exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(10)


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_relogin_real_core_load_select_no_stale_shadow(tmp_path, monkeypatch, shared, legacy):
    from agent import credential_pool as core
    from hermes_cli import auth
    root = tmp_path / "auth-root"
    monkeypatch.setenv("HERMES_HOME", str(root))
    if shared:
        monkeypatch.setenv("HERMES_AUTH_HOME", str(root))
    path = root / "auth.json"
    before = seed(path, [row("rcortese", 0, **COOLDOWN),
                         row("viviane", 1, source="device_code", **COOLDOWN)])
    if legacy:
        before["providers"]["openai-codex"] = {"tokens": {
            "access_token": jwt(sub="viviane"), "refresh_token": "stale-shadow"}}
        path.write_text(json.dumps(before))
    fresh = jwt(sub="viviane", generation=2)
    oauth._persist_codex_credentials(root, {"access_token": fresh, "refresh_token": "fresh"})
    saved = json.loads(path.read_text())
    rows = saved["credential_pool"]["openai-codex"]
    assert rows[0] == before["credential_pool"]["openai-codex"][0]
    assert rows[1]["source"] == "manual:device_code"
    for key in ("id", "label", "priority", "created_at", *COOLDOWN):
        assert rows[1][key] == before["credential_pool"]["openai-codex"][1][key]
    assert saved["providers"] == {"other": before["providers"]["other"]}
    # Exercise real lock, disk read, singleton seeding, persistence and selection.
    # Only upstream quota/refresh and external environment seeding are disabled.
    monkeypatch.setattr(core, "_seed_from_env", lambda *a: (False, set()))
    monkeypatch.setattr(auth, "_probe_codex_quota_restored", lambda *a, **kw: False)
    monkeypatch.setattr(core, "_codex_access_token_is_expiring", lambda *a: False)
    pool = core.load_pool("openai-codex")
    assert len(pool._entries) == 2
    assert pool.select() is None  # both original cooldowns remain in force
    current = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert len(current) == 2
    assert [r["id"] for r in current] == [r["id"] for r in rows]
    assert current[1]["access_token"] == fresh
    assert current[1]["refresh_token"] == "fresh"
    assert all(r[k] == v for r in current for k, v in COOLDOWN.items())
    # Repeated login cannot reseed a shadow or append another equivalent row.
    oauth._persist_codex_credentials(root, {"access_token": fresh, "refresh_token": "fresh"})
    assert len(core.load_pool("openai-codex")._entries) == 2


@pytest.mark.parametrize("singleton", [
    {"tokens": {"access_token": jwt(sub="other")}},
    {"tokens": {"access_token": "opaque"}},
    {"tokens": None}, {}, None, [],
])
@pytest.mark.parametrize("empty", [False, True])
def test_unrelated_unknown_singleton_rejected_unchanged(tmp_path, singleton, empty):
    path = tmp_path / "auth.json"
    store = seed(path, [] if empty else None)
    store["providers"]["openai-codex"] = singleton
    path.write_text(json.dumps(store))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="legacy singleton"):
        oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(sub="viviane")})
    assert path.read_bytes() == before


def test_singleton_only_same_principal_migrates(tmp_path):
    path = tmp_path / "auth.json"
    store = seed(path, [])
    store["providers"]["openai-codex"] = {"tokens": {"access_token": jwt()}}
    path.write_text(json.dumps(store))
    oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(generation=2)})
    after = json.loads(path.read_text())
    assert "openai-codex" not in after["providers"]
    assert len(after["credential_pool"]["openai-codex"]) == 1
    assert after["credential_pool"]["openai-codex"][0]["source"] == "manual:device_code"


def test_whitespace_principal_relogin_and_duplicate_rejection(tmp_path):
    path = tmp_path / "auth.json"
    seed(path, [row("viviane", 0)])
    padded = jwt(account=" shared-workspace ", sub=" viviane ", generation=2)
    oauth._persist_codex_credentials(tmp_path, {"access_token": padded})
    rows = json.loads(path.read_text())["credential_pool"]["openai-codex"]
    assert len(rows) == 1 and rows[0]["id"] == "viviane-id"
    seed(path, [row("viviane", 0), row("duplicate", 1, access_token=padded)])
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="duplicate principal"):
        oauth._persist_codex_credentials(tmp_path, {"access_token": jwt(sub="viviane")})
    assert path.read_bytes() == before


def test_claude_link_marker_stays_local_with_shared_codex_root(tmp_path, monkeypatch):
    from api.onboarding import _provider_oauth_authenticated
    home = tmp_path / "profile"
    shared = tmp_path / "shared"
    monkeypatch.setenv("HERMES_AUTH_HOME", str(shared))
    seed(shared / "auth.json")
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"credential_pool": {"anthropic": [
        {"auth_type": "oauth", "source": "claude_code_linked"}]}}))
    assert _provider_oauth_authenticated("anthropic", home)
    assert _provider_oauth_authenticated("openai-codex", home)


@pytest.mark.parametrize("providers", [None, [], "invalid"])
def test_invalid_providers_rejected_without_write(tmp_path, providers):
    path = tmp_path / "auth.json"
    store = seed(path)
    store["providers"] = providers
    path.write_text(json.dumps(store))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="providers"):
        oauth._persist_codex_credentials(tmp_path, {"access_token": jwt()})
    assert path.read_bytes() == before
