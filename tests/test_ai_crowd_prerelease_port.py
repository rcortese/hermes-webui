"""Port regressions at the new prerelease ownership boundaries; synthetic state only."""
from types import SimpleNamespace

import pytest


def test_restart_resolves_remote_config_without_local_home(monkeypatch):
    from api import config, gateway_chat, profiles
    cfg = {"webui": {"profile_proxies": {"remote": {
        "base_url": "https://remote.invalid", "api_key": "fixture-key",
    }}}}
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(profiles, "list_profiles_api", lambda **kw: [])
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda *a: pytest.fail("remote has no local home"))
    assert gateway_chat._gateway_endpoint_for_profile("remote") == ("https://remote.invalid", "fixture-key")
    monkeypatch.setattr(profiles, "list_profiles_api", lambda **kw: [{"name": "REMOTE"}])
    with pytest.raises(ValueError, match="both local and remote"):
        gateway_chat._gateway_endpoint_for_profile("remote")
    monkeypatch.setattr(profiles, "list_profiles_api", lambda **kw: [])
    del cfg["webui"]["profile_proxies"]["remote"]["api_key"]
    with pytest.raises(ValueError, match="credential"):
        gateway_chat._gateway_endpoint_for_profile("remote")


def test_config_only_proxy_is_visible_to_switch_resolver(monkeypatch):
    from api import config, profile_proxy
    monkeypatch.setattr(config, "get_config", lambda: {"webui": {"profile_proxies": {
        "remote": {"base_url": "https://remote.invalid", "api_key": "fixture-key"},
    }}})
    assert profile_proxy.profile_proxy_for("REMOTE")["name"] == "remote"


def test_regeneration_receives_resolved_remote_target_before_mutation(monkeypatch):
    from contextlib import nullcontext
    from api import routes
    cfg = {"webui": {"profile_proxies": {"remote": {
        "base_url": "https://remote.invalid", "api_key": "fixture-key",
    }}}}
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    monkeypatch.setattr(routes, "list_profiles_api", lambda **kw: [])
    monkeypatch.setattr(routes, "_agent_runtime_barrier_response", lambda **kw: None if kw["external_runtime_owned"] else pytest.fail("wrong owner"))
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda sid: nullcontext())
    monkeypatch.setattr(routes, "_active_run_stream_for_session", lambda sid: None)
    monkeypatch.setattr(routes, "_start_regeneration_stream_locked", lambda session, **kw: kw)
    s = SimpleNamespace(session_id="fixture-regeneration", profile="remote", active_stream_id=None)
    result = routes._start_chat_stream_for_session(s, msg="hello", workspace="/unused", model="fixture", regeneration=object())
    assert result["backend_is_gateway"] is True
    assert result["gateway_config"]["base_url"] == "https://remote.invalid"
    assert result["gateway_config"]["api_key"] == "fixture-key"


def test_checkpoint_bridge_never_overrides_explicit_turn_identity():
    from api import streaming
    previous = [{"role": "user", "content": "same text"}]
    merged = [*previous, {"role": "assistant", "content": "old answer"}]
    assert streaming._turn_transcript_lacks_final_assistant_answer(
        merged, previous, "same text", active_turn_identity={"turn_id": "new-turn"},
    ) is True
