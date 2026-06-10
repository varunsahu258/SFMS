"""Administrator fee-notice PDF generation window for SFMS."""

from __future__ import annotations

import os
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from report_generator import fee_notice_pdf


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for notice generation."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class FeeNoticeWindow(tk.Toplevel):
    """Generate one-page-per-student fee notices for a selected class."""

    @auth.require_permission("issue_fee_notices")
    def __init__(self, master=None):
        """Create the class selector and generation action."""
        super().__init__(master)
        self.title("Fee Notices")
        self.geometry("460x250")
        self.configure(bg=SPLASH_BG)
        self.class_var = tk.StringVar()
        with _connect() as conn:
            classes = [row[0] for row in conn.execute("SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> '' ORDER BY class")]
        tk.Label(self, text="Fee Notices", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 18, "bold")).pack(pady=(28, 18))
        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack()
        tk.Label(form, text="Class", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=8)
        combo = ttk.Combobox(form, textvariable=self.class_var, values=classes, state="readonly", width=25)
        combo.pack(side="left")
        if classes:
            self.class_var.set(classes[0])
        ttk.Button(self, text="Generate Notices", command=self.generate).pack(pady=24)

    def generate(self) -> None:
        """Generate and open the selected class's fee-notice PDF."""
        auth.touch_session()
        class_name = self.class_var.get().strip()
        if not class_name:
            messagebox.showerror("Fee notices", "Select a class.", parent=self)
            return
        try:
            with _connect() as conn:
                path = fee_notice_pdf(conn, class_name)
        except Exception as exc:
            messagebox.showerror("Fee notices", str(exc), parent=self)
            return
        if hasattr(os, "startfile"):
            os.startfile(path)
        messagebox.showinfo("Fee notices", f"PDF saved to:\n{path}", parent=self)
