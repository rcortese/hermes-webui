import importlib
from urllib.parse import urlparse


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeKB:
    def __init__(self, owner="jen"):
        self.owner = owner
        self.dispatched = False

    def _normalize_board_slug(self, slug):
        return str(slug or '').strip()

    def board_exists(self, slug):
        return bool(slug)

    def connect(self, board=None):
        return FakeConn()

    def board_metadata(self, board):
        return {"slug": board or "default", "dispatch_owner": self.owner}

    def normalize_dispatch_owner(self, value):
        text = str(value or "").strip().lower()
        return text or None

    def _coerce_dispatch_unowned(self, value):
        return str(value).lower() in {"1", "true", "yes", "on"}

    def board_dispatchability(self, meta, *, dispatch_owner=None, dispatch_unowned_boards=True):
        owner = meta.get("dispatch_owner")
        dispatchable = (not dispatch_owner) or owner == dispatch_owner or (not owner and dispatch_unowned_boards)
        return {
            "dispatch_owner": owner,
            "dispatchable": dispatchable,
            "dispatchability_warning": None if dispatchable else f"owned by {owner}; dispatcher owner is {dispatch_owner}",
        }

    def dispatch_once(self, conn, dry_run=False, max_spawn=8):
        self.dispatched = True
        return {"spawned": ["t_ok"], "dry_run": dry_run}

    def known_assignees(self, conn):
        return []

    def list_boards(self, include_archived=False):
        return [
            {"slug": "ops", "dispatch_owner": self.owner},
            {"slug": "infra", "dispatch_owner": "moss"},
            {"slug": "scratch", "dispatch_owner": None},
        ]


def _bridge(monkeypatch, fake):
    bridge = importlib.import_module("api.kanban_bridge")
    monkeypatch.setattr(bridge, "_kb", lambda: fake)
    monkeypatch.setattr(bridge, "_conn", lambda board=None: fake.connect(board=board))
    return bridge


def test_dispatch_refuses_other_owned_board_before_dispatch_once(monkeypatch):
    fake = FakeKB(owner="jen")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_OWNER", "moss")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_UNOWNED_BOARDS", "false")
    bridge = _bridge(monkeypatch, fake)

    payload = bridge._dispatch_payload(urlparse("/api/kanban/dispatch?board=ops&max=8"))

    assert fake.dispatched is False
    assert payload["dispatchable"] is False
    assert payload["dispatch_owner"] == "jen"
    assert payload["spawned"] == []
    assert payload["skipped_dispatch"]
    assert "dispatcher owner is moss" in payload["dispatchability_warning"]


def test_dispatch_allows_matching_owned_board(monkeypatch):
    fake = FakeKB(owner="moss")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_OWNER", "moss")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_UNOWNED_BOARDS", "false")
    bridge = _bridge(monkeypatch, fake)

    payload = bridge._dispatch_payload(urlparse("/api/kanban/dispatch?board=ops&dry_run=1&max=8"))

    assert fake.dispatched is True
    assert payload["spawned"] == ["t_ok"]
    assert payload["dry_run"] is True


def test_config_payload_exposes_policy_and_known_dispatch_owners(monkeypatch):
    fake = FakeKB(owner="jen")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_OWNER", "moss")
    monkeypatch.setenv("HERMES_KANBAN_DISPATCH_UNOWNED_BOARDS", "false")
    bridge = _bridge(monkeypatch, fake)

    payload = bridge._config_payload(board="ops")

    assert payload["dispatch_owner"] == "moss"
    assert payload["dispatch_unowned_boards"] is False
    assert payload["dispatch_policy_source"] == "env"
    assert payload["known_dispatch_owners"] == ["jen", "moss"]
