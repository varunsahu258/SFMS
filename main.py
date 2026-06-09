"""Application startup for SFMS."""

from __future__ import annotations

import sqlite3
import tkinter as tk

from config import (
    APP_SUBTITLE,
    APP_TITLE,
    DB_PATH,
    SCHOOL_NAME,
    SETTING_SCHOOL_NAME,
    SPLASH_BG,
    SPLASH_DURATION_MS,
    SPLASH_FG,
)
from database import init_db


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply required SQLite pragmas for this database connection."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")


def _school_name() -> str:
    """Return the configured school name for the splash screen."""
    with sqlite3.connect(DB_PATH) as conn:
        _apply_pragmas(conn)
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING_SCHOOL_NAME,)).fetchone()
    return row[0] if row and row[0] else SCHOOL_NAME


def _open_login(root: tk.Tk) -> None:
    """Close the splash window and open the login window."""
    root.destroy()
    from ui_login import LoginWindow

    LoginWindow()


def show_splash() -> None:
    """Show the two-second SFMS splash screen before login."""
    root = tk.Tk()
    root.title(APP_TITLE)
    root.configure(background=SPLASH_BG)
    root.overrideredirect(True)

    width = 520
    height = 300
    x_position = (root.winfo_screenwidth() - width) // 2
    y_position = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x_position}+{y_position}")

    frame = tk.Frame(root, background=SPLASH_BG)
    frame.pack(expand=True, fill="both")

    tk.Label(
        frame,
        text=_school_name(),
        font=("Segoe UI", 18, "bold"),
        bg=SPLASH_BG,
        fg=SPLASH_FG,
        wraplength=460,
    ).pack(pady=(72, 12))
    tk.Label(
        frame,
        text=APP_TITLE,
        font=("Segoe UI", 44, "bold"),
        bg=SPLASH_BG,
        fg=SPLASH_FG,
    ).pack()
    tk.Label(
        frame,
        text=APP_SUBTITLE,
        font=("Segoe UI", 13),
        bg=SPLASH_BG,
        fg=SPLASH_FG,
    ).pack(pady=(8, 0))

    root.after(SPLASH_DURATION_MS, lambda: _open_login(root))
    root.mainloop()


def main() -> None:
    """Initialize the database and start the desktop application."""
    init_db()
    show_splash()


if __name__ == "__main__":
    main()
