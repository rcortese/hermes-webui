import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "static" / "panels.js"
INDEX = ROOT / "static" / "index.html"


def _panels():
    return PANELS.read_text(encoding="utf-8")


def _index():
    return INDEX.read_text(encoding="utf-8")


def test_board_modal_owner_control_is_select_with_clear_and_free_text_fallback():
    html = _index()
    assert 'id="kanbanBoardModalOwner"' in html
    assert re.search(r"<select[^>]+id=\"kanbanBoardModalOwner\"", html), "owner control should be a select populated from API owner choices"
    assert 'id="kanbanBoardModalOwnerCustom"' in html, "free-text fallback should exist for owners not in the select"
    assert 'value="__custom__"' in html
    assert 'value=""' in html, "blank/clear option should represent unowned"


def test_frontend_uses_api_owner_choices_to_populate_board_owner_select():
    src = _panels()
    assert "known_dispatch_owners" in src
    assert "_kanbanPopulateBoardOwnerControl" in src
    assert "kanbanBoardModalOwnerCustom" in src


def test_dispatch_result_surfaces_dispatchability_warning_and_skipped_dispatch():
    src = _panels()
    start = src.index("function _kanbanFormatDispatchResult")
    end = src.index("function _kanbanSelectedTaskIds", start)
    body = src[start:end]
    assert "dispatchability_warning" in body
    assert "skipped_dispatch" in body
    assert "dispatchable" in body


def test_run_dispatcher_checks_active_board_dispatchability_before_confirm():
    src = _panels()
    start = src.index("async function runKanbanDispatcher")
    end = src.index("function _setKanbanDispatcherButtonsDisabled", start)
    body = src[start:end]
    assert "_kanbanActiveBoardDispatchability" in src
    assert "dispatchable === false" in body
    assert "showConfirmDialog" in body
    assert body.index("dispatchable === false") < body.index("showConfirmDialog")


def test_board_menu_renders_owner_and_dispatchability_badges_not_just_warning_icon():
    src = _panels()
    start = src.index("function _renderKanbanBoardMenu")
    end = src.index("function toggleKanbanBoardMenu", start)
    body = src[start:end]
    assert "kanban-board-owner-badge" in body
    assert "kanban-board-dispatch-badge" in body
    assert "dispatchability_warning" in body
