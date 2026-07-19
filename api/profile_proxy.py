"""Fail-closed remote persona proxy configuration for WebUI chat."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

_PROFILE_PROXY_ENV_PREFIX = "HERMES_WEBUI_PROFILE_PROXY_"


def _normalized_proxy_url(value: object) -> str | None:
    """Accept only credential-free HTTP(S) proxy bases with a usable host."""
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return None
    try:
        parsed = urlparse(base_url)
        parsed.port  # validates numeric range and malformed ports
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.query or parsed.fragment:
        return None
    return base_url


def _public_host(base_url: str) -> str | None:
    """Return only host[:port], never userinfo or URL path."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    rendered = f"[{host}]" if ":" in host else host
    return f"{rendered}:{port}" if port is not None else rendered


def profile_name_key(name: str | None) -> str:
    return str(name or "").strip().casefold()


def profile_proxy_entries(config_data=None, environ: dict[str, str] | None = None) -> dict[str, dict]:
    """Read configured remote targets. Values are private and not selector rows."""
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    webui_cfg = cfg.get("webui") if isinstance(cfg.get("webui"), dict) else {}
    raw = webui_cfg.get("profile_proxies") if isinstance(webui_cfg, dict) else cfg.get("webui_profile_proxies")
    entries: dict[str, dict] = {}
    if isinstance(raw, dict):
        for name, item in raw.items():
            if not isinstance(item, dict):
                continue
            key = profile_name_key(name)
            base_url = _normalized_proxy_url(item.get("base_url"))
            if not key or not base_url:
                continue
            api_key_env = str(item.get("api_key_env") or "").strip()
            api_key = str(source.get(api_key_env) or "").strip() if api_key_env else str(item.get("api_key") or "").strip()
            entries[key] = {
                "name": str(name).strip(), "label": str(item.get("label") or name).strip() or str(name).strip(),
                "base_url": base_url, "api_key": api_key, "api_key_configured": bool(api_key),
                "remote_profile": str(item.get("remote_profile") or name).strip() or str(name).strip(),
                "session_key_prefix": str(item.get("session_key_prefix") or f"webui:{name}").strip() or f"webui:{name}",
            }
    for env_name, value in source.items():
        if not env_name.startswith(_PROFILE_PROXY_ENV_PREFIX) or not env_name.endswith("_BASE_URL"):
            continue
        token = env_name[len(_PROFILE_PROXY_ENV_PREFIX):-len("_BASE_URL")]
        key = profile_name_key(token.replace("_", "-"))
        base_url = _normalized_proxy_url(value)
        if not token or not base_url:
            continue
        prefix = f"{_PROFILE_PROXY_ENV_PREFIX}{token}_"
        api_key_env = str(source.get(prefix + "API_KEY_ENV") or "").strip()
        api_key = str(source.get(api_key_env) or "").strip() if api_key_env else str(source.get(prefix + "API_KEY") or "").strip()
        entries[key] = {
            "name": key, "label": str(source.get(prefix + "LABEL") or key).strip() or key,
            "base_url": base_url, "api_key": api_key, "api_key_configured": bool(api_key),
            "remote_profile": str(source.get(prefix + "REMOTE_PROFILE") or key).strip() or key,
            "session_key_prefix": str(source.get(prefix + "SESSION_KEY_PREFIX") or f"webui:{key}").strip() or f"webui:{key}",
        }
    return entries


def profile_proxy_for(name: str, config_data=None, environ: dict[str, str] | None = None) -> dict | None:
    return profile_proxy_entries(config_data, environ).get(profile_name_key(name))


def profile_proxy_public_entries(config_data=None, environ: dict[str, str] | None = None) -> list[dict]:
    """Return sanitized selector rows without URL paths or credentials."""
    rows = []
    for item in profile_proxy_entries(config_data, environ).values():
        host = _public_host(str(item.get("base_url") or ""))
        rows.append({
            "name": item["name"], "label": item.get("label") or item["name"], "path": None,
            "is_default": False, "is_active": False, "gateway_running": True,
            "model": item.get("remote_profile") or item["name"], "provider": "remote-gateway",
            "has_env": bool(item.get("api_key_configured")), "visible": True,
            "skill_count": 0, "enabled_skills": 0, "total_skills": 0,
            "remote_proxy": True, "profile_kind": "remote_gateway_proxy",
            "base_url_host_only": host, "remote_profile": item.get("remote_profile") or item["name"],
            "backend": "remote_gateway", "backend_label": "remote gateway",
        })
    return sorted(rows, key=lambda row: profile_name_key(row.get("name")))


def resolve_execution_target(selected_profile: str | None, *, local_gateway_enabled: bool, config_data=None, environ: dict[str, str] | None = None, profiles: list[dict] | None = None, local_gateway_config: dict | None = None) -> dict[str, Any]:
    """Resolve target ownership and reject unknown or colliding remote identities."""
    selected = str(selected_profile or "").strip() or "default"
    key = profile_name_key(selected)
    local = next((dict(row) for row in profiles or [] if not row.get("remote_proxy") and profile_name_key(row.get("name")) == key), None)
    proxy = profile_proxy_for(selected, config_data, environ)
    if proxy and local:
        return {"ok": False, "_status": 409, "error_type": "ambiguous_profile", "execution_target": "fail_closed", "error": "selected profile is both local and remote"}
    if proxy:
        if not proxy.get("api_key_configured"):
            return {"ok": False, "_status": 503, "error_type": "remote_profile_auth_unconfigured", "execution_target": "fail_closed", "error": "selected remote profile has no configured gateway credential"}
        return {"ok": True, "execution_target": "remote_gateway", "profile_kind": "remote_gateway_proxy", "gateway_config": dict(proxy)}
    if local or key == "default":
        return {"ok": True, "execution_target": "local_gateway" if local_gateway_enabled else "local_direct", "profile_kind": "local_profile", "gateway_config": local_gateway_config if local_gateway_enabled else None}
    return {"ok": False, "_status": 404, "error_type": "profile_not_configured", "execution_target": "fail_closed", "error": "selected profile is not configured"}
