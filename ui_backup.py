"""On-demand SQLite backup window used by reminders and the dashboard."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import auth
from config import BACKUPS_DIR, DB_PATH, SPLASH_BG, SPLASH_FG
from utils import now_str


class BackupWindow(tk.Toplevel):
    """Create a consistent SQLite backup and record it in backups_log."""

    def __init__(self, master=None):
        """Create the backup confirmation window."""
        super().__init__(master)
        self.title("Database Backup")
        self.geometry("430x220")
        self.configure(bg=SPLASH_BG)
        self.resizable(False, False)
        tk.Label(self, text="Database Backup", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 18, "bold")).pack(pady=(28, 12))
        tk.Label(self, text="Create a safe copy of the current SFMS database.", bg=SPLASH_BG, fg=SPLASH_FG).pack(pady=8)
        ttk.Button(self, text="Backup Now", command=self.create_backup).pack(pady=18)

    def create_backup(self) -> None:
        """Use SQLite's backup API and append a successful backup log row."""
        auth.touch_session()
        Path(BACKUPS_DIR).mkdir(parents=True, exist_ok=True)
        filename = f"sfms_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        path = str(Path(BACKUPS_DIR) / filename)
        try:
            with sqlite3.connect(DB_PATH) as source:
                source.execute("PRAGMA foreign_keys=ON")
                source.execute("PRAGMA journal_mode=WAL")
                with sqlite3.connect(path) as destination:
                    source.backup(destination)
                source.execute(
                    "INSERT INTO backups_log (filename, created_at, created_by, type) VALUES (?, ?, ?, 'MANUAL')",
                    (path, now_str(), auth.CURRENT_SESSION.username if auth.CURRENT_SESSION else "SYSTEM"),
                )
        except sqlite3.Error as exc:
            messagebox.showerror("Backup", str(exc), parent=self)
            return
        messagebox.showinfo("Backup", f"Backup created:\n{path}", parent=self)
        self.destroy()
