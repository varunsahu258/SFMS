"""Appearance customization defaults for the shared UI theme."""

from __future__ import annotations

import sqlite3

from migrations import migration_v020_appearance_permissions
from ui_theme import PALETTES, _custom_palette


def test_v020_seeds_theme_customization_defaults():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    migration_v020_appearance_permissions(conn)
    values = dict(conn.execute("SELECT key,value FROM settings"))
    assert values["ui_font_family"] == "Segoe UI"
    assert values["ui_font_size"] == "10"
    assert values["ui_custom_accent"] == "#5b3fc0"


def test_theme_catalog_includes_beautified_presets_and_custom_palette():
    assert {"light", "dark", "ocean", "forest", "rose"} <= set(PALETTES)
    palette = _custom_palette({
        "ui_custom_bg": "#111111",
        "ui_custom_fg": "#eeeeee",
        "ui_custom_card": "#ffffff",
        "ui_custom_accent": "#336699",
    })
    assert palette["bg"] == "#111111"
    assert palette["accent"] == "#336699"
    assert palette["accent_hover"] != palette["accent"]
