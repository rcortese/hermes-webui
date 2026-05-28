from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_kanban_bridge_passes_dispatch_owner_fields():
    src = (ROOT / "api" / "kanban_bridge.py").read_text()
    assert 'if "dispatch_owner" in body:' in src
    assert 'update_kwargs["dispatch_owner"]' in src
    assert 'dispatch_owner=body.get("dispatch_owner") if "dispatch_owner" in body else None' not in src
    assert "board_dispatchability" in src
    assert "dispatchability_warning" in src


def test_static_owner_controls_and_badges_present():
    index = (ROOT / "static" / "index.html").read_text()
    panels = (ROOT / "static" / "panels.js").read_text()
    style = (ROOT / "static" / "style.css").read_text()
    assert "kanbanBoardModalOwner" in index
    assert "dispatch_owner" in panels
    assert "kanban-board-owner-badge" in panels
    assert "dispatchability_warning" in panels
    assert ".kanban-board-owner-badge" in style
