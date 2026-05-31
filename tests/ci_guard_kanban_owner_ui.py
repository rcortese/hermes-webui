from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "static" / "panels.js"
STYLE = ROOT / "static" / "style.css"


def main() -> None:
    src = PANELS.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    required = [
        "function _kanbanLabel",
        "val !== key",
        "kanban-board-switcher-item-body",
        "kanban-board-switcher-item-meta",
    ]
    for needle in required:
        if needle not in src and needle not in css:
            raise SystemExit(f"missing Kanban owner UI guard: {needle}")

    forbidden = [
        "t('kanban_dispatch_owner') || 'Owner'",
        "t('kanban_dispatch_owner_unowned') || 'Unowned'",
        "t('kanban_dispatch_not_dispatchable') || 'Not dispatchable'",
        "t('kanban_dispatch_dispatchable') || 'Dispatchable'",
    ]
    for needle in forbidden:
        if needle in src:
            raise SystemExit(f"raw-i18n fallback regression in board switcher: {needle}")

    print("Kanban owner UI guard ok")


if __name__ == "__main__":
    main()
