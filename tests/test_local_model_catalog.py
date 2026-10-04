"""Profile-local picker curation applies after cache reads, without changing routing."""
import copy
import json


def test_grouped_and_live_catalog(tmp_path):
    from api.model_catalog import apply_local_catalog
    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps({"openai-codex": {
        "add": ["gpt-6-sol", "gpt-6-luna"],
        "hide": ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5"],
    }}))
    rows = [{"id": s, "label": s} for s in [
        "gpt-6-astra", "gpt-6-astra-900k", "gpt-5.6-sol",
        "gpt-5.6-sol-900k", "gpt-5.6-luna", "gpt-5.5", "gpt-5.5-900k",
        "gpt-5.6-terra", "gpt-6-sol",
    ]]
    original = {"active_provider": "openai-codex", "default_model": "gpt-5.5", "groups": [
        {"provider_id": "openai-codex", "models": rows[:4], "extra_models": rows[4:]},
        {"provider_id": "other", "models": copy.deepcopy(rows)},
    ]}
    before = copy.deepcopy(original)
    result = apply_local_catalog(original, path)
    group = result["groups"][0]
    ids = [r["id"] for r in group["models"] + group["extra_models"]]
    assert set(ids) == {"gpt-6-astra", "gpt-6-astra-900k", "gpt-5.6-terra", "gpt-6-sol", "gpt-6-luna"}
    assert len(ids) == len(set(ids))
    assert original == before
    assert result["default_model"] == "gpt-5.5"
    assert result["groups"][1] == original["groups"][1]
    live = apply_local_catalog({"provider": "openai-codex", "models": rows, "count": len(rows)}, path)
    assert {r["id"] for r in live["models"]} == set(ids)
    assert live["count"] == len(live["models"])


def test_profile_missing_invalid_and_hot_reload(tmp_path):
    from api.model_catalog import apply_local_catalog
    path = tmp_path / "model_catalog.json"
    data = {"provider": "openai-codex", "models": [{"id": "x"}], "count": 1}
    for content in [None, "bad json", "[]", '{"openai-codex": {"add": "bad", "hide": "x"}}']:
        if content is not None:
            path.write_text(content)
        assert apply_local_catalog(data, path) == data
    path.write_text('{"openai-codex":{"hide":["x"],"add":["y",null,{},"y"]}}')
    assert apply_local_catalog(data, path)["models"] == [{"id": "y", "label": "y"}]
    assert apply_local_catalog(data, tmp_path / "other-profile.json") == data
    path.write_text('{"openai-codex":{"add":["z"]}}')
    assert [m["id"] for m in apply_local_catalog(data, path)["models"]] == ["x", "z"]


def test_provider_prefixes(tmp_path):
    from api.model_catalog import apply_local_catalog
    path = tmp_path / "model_catalog.json"
    path.write_text('{"openai-codex":{"hide":["gpt-5.5"],"add":["gpt-6-sol"]}}')
    data = {"active_provider": "other", "groups": [{"provider_id": "openai-codex", "models": [
        {"id": "@openai-codex:gpt-5.5-900k"}, {"id": "openai-codex/gpt-6-sol"}
    ]}]}
    result = apply_local_catalog(data, path)
    assert result["groups"][0]["models"] == [{"id": "openai-codex/gpt-6-sol"}]
    data["groups"][0]["models"] = []
    assert apply_local_catalog(data, path)["groups"][0]["models"][0]["id"] == "openai-codex/gpt-6-sol"
