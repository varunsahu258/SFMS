"""Theme and language preference loading for the SFMS Tk application."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import font as tkfont, ttk

from config import DB_PATH

# The light palette mirrors the dashboard and Main Collection workspace.  Keeping
# it here makes every ttk-based page, dialog, table, and form use one visual system.
LIGHT = {
    "bg": "#f5f3fa", "fg": "#201a2b", "field": "#ffffff",
    "select": "#5b3fc0", "muted": "#766f80", "border": "#e4deef",
    "card": "#ffffff", "accent": "#5b3fc0", "accent_hover": "#49309f",
}
DARK = {
    "bg": "#17131f", "fg": "#f7f4fb", "field": "#272131",
    "select": "#8067df", "muted": "#bbb2c8", "border": "#3b3348",
    "card": "#211b2a", "accent": "#8067df", "accent_hover": "#927ce8",
}
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
    """Apply the shared dashboard-style palette and return language/font."""
    stored_theme, stored_language = _preferences()
    theme = theme if theme in ("dark", "light") else stored_theme
    palette = DARK if theme == "dark" else LIGHT
    root._sfms_theme = theme
    root._sfms_palette = palette
    style = ttk.Style(root)
    style.theme_use("clam")
    resolved_language, font_family = resolve_language(root, language or stored_language)

    style.configure(".", background=palette["bg"], foreground=palette["fg"],
                    font=(font_family, 10))
    style.configure("TFrame", background=palette["bg"])
    style.configure("Card.TFrame", background=palette["card"], relief="flat")
    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
    style.configure("Title.TLabel", font=(font_family, 20, "bold"), foreground=palette["fg"])
    style.configure("Muted.TLabel", foreground=palette["muted"])
    style.configure("TButton", background=palette["field"], foreground=palette["fg"],
                    bordercolor=palette["border"], lightcolor=palette["field"],
                    darkcolor=palette["field"], padding=(14, 8), relief="flat")
    style.map("TButton",
              background=[("pressed", palette["border"]), ("active", palette["border"])],
              foreground=[("disabled", palette["muted"]), ("active", palette["fg"])])
    style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                    bordercolor=palette["accent"], padding=(16, 9), font=(font_family, 10, "bold"))
    style.map("Accent.TButton",
              background=[("disabled", palette["muted"]), ("pressed", palette["accent_hover"]),
                          ("active", palette["accent_hover"])],
              foreground=[("disabled", "#e7e3ec"), ("active", "#ffffff")])
    style.configure("TEntry", fieldbackground=palette["field"], foreground=palette["fg"],
                    bordercolor=palette["border"], padding=7)
    style.configure("TCombobox", fieldbackground=palette["field"], foreground=palette["fg"],
                    background=palette["field"], bordercolor=palette["border"], padding=6)
    style.map("TCombobox", fieldbackground=[("readonly", palette["field"])],
              foreground=[("readonly", palette["fg"])])
    style.configure("TCheckbutton", background=palette["bg"], foreground=palette["fg"], padding=4)
    style.configure("TRadiobutton", background=palette["bg"], foreground=palette["fg"], padding=4)
    style.configure("TNotebook", background=palette["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=palette["border"], foreground=palette["fg"],
                    padding=(15, 8), font=(font_family, 10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", palette["card"]), ("active", palette["field"])],
              foreground=[("selected", palette["accent"])])
    style.configure("Treeview", background=palette["card"], fieldbackground=palette["card"],
                    foreground=palette["fg"], rowheight=30, bordercolor=palette["border"], relief="flat")
    style.configure("Treeview.Heading", background=palette["border"], foreground=palette["fg"],
                    font=(font_family, 9, "bold"), padding=(8, 7), relief="flat")
    style.map("Treeview", background=[("selected", palette["select"])],
              foreground=[("selected", "#ffffff")])
    style.configure("Vertical.TScrollbar", background=palette["border"], troughcolor=palette["bg"],
                    bordercolor=palette["bg"], arrowcolor=palette["muted"])

    root.option_add("*Font", (font_family, 10))
    root.option_add("*Background", palette["bg"])
    root.option_add("*Foreground", palette["fg"])
    root.option_add("*Text.background", palette["field"])
    root.option_add("*Text.foreground", palette["fg"])
    root.option_add("*Listbox.background", palette["field"])
    root.option_add("*Listbox.foreground", palette["fg"])
    try:
        root.configure(background=palette["bg"])
    except tk.TclError:
        pass
    return resolved_language, font_family
