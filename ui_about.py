"""SFMS application and environment information dialog."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from config import APP_VERSION, DB_PATH, SCHOOL_NAME
from ui_theme import apply_theme


class AboutDialog(tk.Toplevel):
    """Show version, school, database, and Python runtime details."""

    def __init__(self, master=None):
        super().__init__(master)
        apply_theme(self)
        self.title("About SFMS")
        self.geometry("560x340")
        self.resizable(False, False)
        self.transient(master)
        database = Path(DB_PATH).resolve()
        size = database.stat().st_size if database.exists() else 0
        school_name = SCHOOL_NAME
        if database.exists():
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='school_name'").fetchone()
                if row and row[0]:
                    school_name = row[0]
        ttk.Label(self, text="SFMS", font=("Segoe UI", 24, "bold")).pack(pady=(24, 5))
        ttk.Label(self, text=school_name, font=("Segoe UI", 12, "bold")).pack(pady=3)
        details = (
            f"Version: {APP_VERSION}\n\nDatabase: {database}\nDatabase size: {size / 1048576:,.2f} MB\n"
            f"Python: {platform.python_version()}"
        )
        ttk.Label(self, text=details, justify="left", wraplength=500).pack(anchor="w", padx=30, pady=20)
        ttk.Button(self, text="Open DB Folder", command=self.open_folder).pack(pady=8)

    def open_folder(self) -> None:
        folder = str(Path(DB_PATH).resolve().parent)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as exc:
            messagebox.showerror("Open Folder", str(exc), parent=self)
