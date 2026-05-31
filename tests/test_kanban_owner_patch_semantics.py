import importlib


class FakeKB:
    DEFAULT_BOARD = "default"

    def __init__(self):
        self.meta = {"slug": "ops", "name": "Ops", "dispatch_owner": "moss"}
        self.write_calls = []

    def _normalize_board_slug(self, slug):
        return str(slug).strip()

    def board_exists(self, slug):
        return slug == "ops"

    def write_board_metadata(self, slug, **kwargs):
        self.write_calls.append(kwargs)
        if "dispatch_owner" in kwargs:
            self.meta["dispatch_owner"] = kwargs["dispatch_owner"]
        for key, value in kwargs.items():
            if key != "dispatch_owner" and value is not None:
                self.meta[key] = value
        return dict(self.meta)

    def normalize_dispatch_owner(self, value):
        text = str(value or "").strip().lower()
        return text or None

    def board_dispatchability(self, meta, *, dispatch_owner=None, dispatch_unowned_boards=True):
        owner = meta.get("dispatch_owner")
        dispatchable = (not dispatch_owner) or owner == dispatch_owner or (not owner and dispatch_unowned_boards)
        return {"dispatch_owner": owner, "dispatchable": dispatchable, "dispatchability_warning": None if dispatchable else "blocked"}


def _bridge(monkeypatch):
    bridge = importlib.import_module("api.kanban_bridge")
    fake = FakeKB()
    monkeypatch.setattr(bridge, "_kb", lambda: fake)
    return bridge, fake


def test_patch_board_preserves_dispatch_owner_when_omitted(monkeypatch):
    bridge, fake = _bridge(monkeypatch)

    payload = bridge._update_board_payload("ops", {"name": "Ops renamed"})

    assert payload["board"]["dispatch_owner"] == "moss"
    assert "dispatch_owner" not in fake.write_calls[-1]


def test_patch_board_clears_dispatch_owner_only_when_explicitly_blank(monkeypatch):
    bridge, fake = _bridge(monkeypatch)

    payload = bridge._update_board_payload("ops", {"name": "Ops", "dispatch_owner": ""})

    assert payload["board"]["dispatch_owner"] is None
    assert fake.write_calls[-1]["dispatch_owner"] is None


def test_patch_board_clears_dispatch_owner_only_when_explicitly_null(monkeypatch):
    bridge, fake = _bridge(monkeypatch)

    payload = bridge._update_board_payload("ops", {"dispatch_owner": None})

    assert payload["board"]["dispatch_owner"] is None
    assert fake.write_calls[-1]["dispatch_owner"] is None
