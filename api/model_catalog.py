"""Profile-local display curation, independent of provider discovery caches.

This is a picker preference, not an authorization or model-routing boundary.
"""
import copy
import json
from pathlib import Path


def apply_local_catalog(payload: dict, path: Path | None = None) -> dict:
    """Apply model_catalog.json beside the active config to a catalog response.

    Read after every cache hit so settings changes need no restart. Never mutate
    the shared discovery cache or the configured main/auxiliary assignments.
    """
    if path is None:
        from api.config import _get_config_path
        path = _get_config_path().with_name("model_catalog.json")
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return payload
    if not isinstance(rules, dict):
        return payload
    result = copy.deepcopy(payload)

    def curate(group, provider, prefix=""):
        rule = rules.get(provider)
        if not isinstance(rule, dict):
            return

        def strings(key):
            values = rule.get(key, [])
            return [s for s in values if isinstance(s, str) and s.strip()] if isinstance(values, list) else []

        hidden = set(strings("hide"))

        def bare(model_id):
            for marker in (f"@{provider}:", f"{provider}/"):
                if model_id.startswith(marker):
                    return model_id[len(marker):]
            return model_id

        def is_hidden(model_id):
            return model_id in hidden or (
                provider == "openai-codex"
                and model_id.endswith("-900k")
                and model_id.removesuffix("-900k") in hidden
            )

        seen = set()
        for bucket in ("models", "extra_models"):
            if bucket not in group:
                continue
            rows = []
            for row in group[bucket]:
                model_id = bare(str(row.get("id", "")))
                if not is_hidden(model_id) and model_id not in seen:
                    seen.add(model_id)
                    rows.append(row)
            group[bucket] = rows
        for model_id in strings("add"):
            if model_id not in seen and not is_hidden(model_id):
                group.setdefault("models", []).append({"id": prefix + model_id, "label": model_id})
                seen.add(model_id)
        if "count" in group:
            group["count"] = len(group.get("models", []))

    if "groups" in result:
        for group in result["groups"]:
            provider = group.get("provider_id", "")
            prefix = "" if provider == result.get("active_provider") else provider + "/"
            curate(group, provider, prefix)
    elif "provider" in result and "models" in result and "error" not in result:
        curate(result, result["provider"])
    return result
