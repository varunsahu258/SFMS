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
OCEAN = {**LIGHT, "bg": "#edf7fb", "fg": "#102a43", "border": "#c8e6f0", "select": "#0f6c81", "accent": "#0f6c81", "accent_hover": "#0a5363"}
FOREST = {**LIGHT, "bg": "#f1f8f2", "fg": "#17351f", "border": "#cfe8d2", "select": "#2d7d46", "accent": "#2d7d46", "accent_hover": "#225f35"}
ROSE = {**LIGHT, "bg": "#fff4f7", "fg": "#3f1724", "border": "#f3c9d5", "select": "#b8325f", "accent": "#b8325f", "accent_hover": "#8f2549"}
PALETTES = {"light": LIGHT, "dark": DARK, "ocean": OCEAN, "forest": FOREST, "rose": ROSE}
DEVANAGARI_FONTS = ("Mangal", "Noto Sans Devanagari", "NotoSansDevanagari")
FONT_STYLES = {"normal": (), "bold": ("bold",), "italic": ("italic",), "bold italic": ("bold", "italic")}


def _preferences() -> dict[str, str]:
    keys = (
        "ui_theme", "ui_language", "ui_font_family", "ui_font_size", "ui_font_style",
        "ui_custom_bg", "ui_custom_fg", "ui_custom_card", "ui_custom_accent",
    )
    try:
        with sqlite3.connect(DB_PATH) as conn:
            placeholders = ",".join("?" for _ in keys)
            rows = dict(conn.execute(f"SELECT key, value FROM settings WHERE key IN ({placeholders})", keys))
    except sqlite3.Error:
        rows = {}
    return {key: str(rows.get(key, "")) for key in keys}


def _shade(hex_color: str, factor: float) -> str:
    color = str(hex_color or "#5b3fc0").lstrip("#")
    if len(color) != 6:
        color = "5b3fc0"
    values = [max(0, min(255, int(int(color[i:i + 2], 16) * factor))) for i in (0, 2, 4)]
    return "#" + "".join(f"{value:02x}" for value in values)


def _custom_palette(preferences: dict[str, str]) -> dict[str, str]:
    bg = preferences.get("ui_custom_bg") or LIGHT["bg"]
    fg = preferences.get("ui_custom_fg") or LIGHT["fg"]
    card = preferences.get("ui_custom_card") or LIGHT["card"]
    accent = preferences.get("ui_custom_accent") or LIGHT["accent"]
    return {
        "bg": bg, "fg": fg, "field": card, "select": accent,
        "muted": _shade(fg, 1.8), "border": _shade(accent, 1.55),
        "card": card, "accent": accent, "accent_hover": _shade(accent, 0.78),
    }


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
    """Apply the shared palette, font size, family, and weight across Tk widgets."""
    preferences = _preferences()
    stored_theme = preferences.get("ui_theme") or "light"
    stored_language = preferences.get("ui_language") or "en"
    theme = theme if theme in (*PALETTES, "custom") else stored_theme
    palette = _custom_palette(preferences) if theme == "custom" else PALETTES.get(theme, LIGHT)
    root._sfms_theme = theme
    root._sfms_palette = palette
    style = ttk.Style(root)
    style.theme_use("clam")
    resolved_language, language_font = resolve_language(root, language or stored_language)
    configured_family = preferences.get("ui_font_family") or language_font
    font_family = language_font if (language or stored_language) == "hi" else configured_family
    try:
        font_size = max(8, min(18, int(preferences.get("ui_font_size") or 10)))
    except ValueError:
        font_size = 10
    font_style = preferences.get("ui_font_style") if preferences.get("ui_font_style") in FONT_STYLES else "normal"
    base_font = (font_family, font_size, *FONT_STYLES[font_style])
    bold_font = (font_family, font_size, "bold")

    style.configure(".", background=palette["bg"], foreground=palette["fg"], font=base_font)
    style.configure("TFrame", background=palette["bg"])
    style.configure("Card.TFrame", background=palette["card"], relief="flat")
    style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
    style.configure("Title.TLabel", font=(font_family, font_size + 10, "bold"), foreground=palette["fg"])
    style.configure("Muted.TLabel", foreground=palette["muted"])
    style.configure("TButton", background=palette["field"], foreground=palette["fg"],
                    bordercolor=palette["border"], lightcolor=palette["field"],
                    darkcolor=palette["field"], padding=(14, 8), relief="flat")
    style.map("TButton",
              background=[("pressed", palette["border"]), ("active", palette["border"])],
              foreground=[("disabled", palette["muted"]), ("active", palette["fg"])])
    style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff",
                    bordercolor=palette["accent"], padding=(16, 9), font=bold_font)
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
                    padding=(15, 8), font=bold_font)
    style.map("TNotebook.Tab", background=[("selected", palette["card"]), ("active", palette["field"])],
              foreground=[("selected", palette["accent"])])
    style.configure("Treeview", background=palette["card"], fieldbackground=palette["card"],
                    foreground=palette["fg"], rowheight=max(28, font_size + 20), bordercolor=palette["border"], relief="flat")
    style.configure("Treeview.Heading", background=palette["border"], foreground=palette["fg"],
                    font=(font_family, max(8, font_size - 1), "bold"), padding=(8, 7), relief="flat")
    style.map("Treeview", background=[("selected", palette["select"])],
              foreground=[("selected", "#ffffff")])
    style.configure("Vertical.TScrollbar", background=palette["border"], troughcolor=palette["bg"],
                    bordercolor=palette["bg"], arrowcolor=palette["muted"])

    root.option_add("*Font", base_font)
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
