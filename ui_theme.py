"""Theme and language preference loading for the SFMS Tk application."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import font as tkfont, ttk

from config import DB_PATH

DARK = {"bg": "#1a1a2e", "fg": "#ffffff", "field": "#2d2d44", "select": "#3d5a99"}
LIGHT = {"bg": "#f0f0f0", "fg": "#000000", "field": "#ffffff", "select": "#3d5a99"}
DEVANAGARI_FONTS = ("Mangal", "Noto Sans Devanagari", "NotoSansDevanagari")


def _preferences() -> tuple[str, str]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = dict(conn.execute("SELECT key, value FROM settings WHERE key IN ('ui_theme','ui_language')"))
    except sqlite3.Error:
        rows = {}
    return rows.get("ui_theme", "light"), rows.get("ui_language", "en")


def resolve_language(root: tk.Misc, requested: str) -> tuple[str, str]:
    """Return a supported language and font, silently falling back to English."""
    if requested != "hi":
        return "en", "Segoe UI"
    families = set(tkfont.families(root))
    for family in DEVANAGARI_FONTS:
        if family in families:
            return "hi", family
    return "en", "Segoe UI"


def apply_theme(root: tk.Misc, theme: str | None = None, language: str | None = None) -> tuple[str, str]:
    """Apply the clam-based dark/light ttk palette and return language/font."""
    stored_theme, stored_language = _preferences()
    theme = theme if theme in ("dark", "light") else stored_theme
    palette = DARK if theme == "dark" else LIGHT
    root._sfms_theme = theme
    root._sfms_palette = palette
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=palette["bg"], foreground=palette["fg"])
    style.configure("TFrame", background=palette["bg"])
    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
    style.configure("TButton", background=palette["field"], foreground=palette["fg"], padding=5)
    style.map("TButton", background=[("active", palette["select"])], foreground=[("active", "#ffffff")])
    style.configure("TEntry", fieldbackground=palette["field"], foreground=palette["fg"])
    style.configure("TCombobox", fieldbackground=palette["field"], foreground=palette["fg"])
    style.configure("Treeview", background=palette["field"], fieldbackground=palette["field"], foreground=palette["fg"])
    style.map("Treeview", background=[("selected", palette["select"])], foreground=[("selected", "#ffffff")])
    root.option_add("*Background", palette["bg"])
    root.option_add("*Foreground", palette["fg"])
    root.option_add("*Text.background", palette["field"])
    root.option_add("*Text.foreground", palette["fg"])
    root.configure(background=palette["bg"])
    resolved_language, font_family = resolve_language(root, language or stored_language)
    style.configure(".", font=(font_family, 10))
    return resolved_language, font_family
