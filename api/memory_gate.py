"""Deployment-level adapter for the optional Moss browser-memory bridge.

This switch selects an integration, not a memory or identity policy. Default to
requiring the existing gate; never infer permission to disable it from a missing
module, profile name, key, policy, or import failure. Restart to change mode.
"""
import os

_MODE = os.environ.get("HERMES_WEBUI_MOSS_MEMORY_GATE", "enabled")
if _MODE not in ("enabled", "disabled"):
    raise ValueError("HERMES_WEBUI_MOSS_MEMORY_GATE must be enabled or disabled")

if _MODE == "enabled":
    # Deliberately eager and uncaught: missing/broken required dependencies must
    # prevent routes from loading, not silently revert to ungated chat.
    from agent.moss_memory_gate import (
        capture_browser as _capture_browser,
        sign_request as _sign_request,
        web_admission as _web_admission,
    )


def capture_browser(fn):
    return _capture_browser(fn) if _MODE == "enabled" else fn


def current_admission():
    return _web_admission.get() if _MODE == "enabled" else None


def sign_request(admission, body, session_id, profile):
    if _MODE == "enabled":
        return _sign_request(admission, body, session_id, profile)
    # Ignore even a stale/internal admission when this integration is off.
    return {}


def request_headers(headers, admission, wire_body, session_id):
    """Only freshly signed admission may introduce a memory-identity proof."""
    clean = {k: v for k, v in headers.items() if k.lower() != "x-moss-memory-proof"}
    return {**clean, **sign_request(
        admission, wire_body, session_id,
        admission["profile"] if admission else "default",
    )}
