"""Pre-login first-time administrator bootstrap screen."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from ui_theme import apply_theme

from config import APP_TITLE, DB_PATH, DEFAULT_ADMIN_USERNAME
from database import create_initial_admin
from security_utils import validate_bootstrap_password


def first_time_setup_required(conn: sqlite3.Connection) -> bool:
    """Return True when no administrator exists."""
    return conn.execute("SELECT 1 FROM users WHERE role='ADMIN' LIMIT 1").fetchone() is None


class FirstTimeSetupWindow(tk.Toplevel):
    """Create the initial administrator before any functional screen is reachable."""

    def __init__(self, master=None, on_complete=None):
        if master is None and tk._default_root is None:
            master = tk.Tk()
            master.withdraw()
        super().__init__(master)
        apply_theme(self)
        self.on_complete = on_complete
        self.title("First-Time Setup")
        self.geometry("540x440")
        self.minsize(500, 400)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.username = tk.StringVar(value=DEFAULT_ADMIN_USERNAME)
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        content = ttk.Frame(self, padding=(28, 24, 28, 12))
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text="First-Time Setup", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            content,
            text="Create the owner administrator account before using SFMS.",
            style="Muted.TLabel", wraplength=460,
        ).grid(row=1, column=0, sticky="w", pady=(0, 18))
        for row, (label, variable, show) in enumerate((
            ("Administrator username", self.username, ""),
            ("Password", self.password, "*"),
            ("Confirm password", self.confirm, "*"),
        ), start=2):
            field = ttk.Frame(content)
            field.grid(row=row, column=0, sticky="ew", pady=(0, 12))
            field.columnconfigure(0, weight=1)
            ttk.Label(field, text=label).grid(row=0, column=0, sticky="w", pady=(0, 4))
            entry = ttk.Entry(field, textvariable=variable, show=show)
            entry.grid(row=1, column=0, sticky="ew")
            if row == 2:
                entry.focus_set()

        # A dedicated non-expanding footer keeps the save action visible at every
        # Windows DPI/scaling setting instead of allowing the form to push it away.
        footer = ttk.Frame(self, padding=(28, 12, 28, 24))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.create_button = ttk.Button(
            footer, text="Create Administrator and Continue",
            command=self._create, style="Accent.TButton",
        )
        self.create_button.grid(row=0, column=0, sticky="ew")
        self.bind("<Return>", lambda _event: self._create())

    def _create(self) -> None:
        ok, message = validate_bootstrap_password(self.password.get())
        if not ok:
            messagebox.showerror("First-Time Setup", message, parent=self)
            return
        if self.password.get() != self.confirm.get():
            messagebox.showerror("First-Time Setup", "Passwords must match.", parent=self)
            return
        try:
            with sqlite3.connect(DB_PATH) as conn:
                create_initial_admin(conn, self.username.get(), self.password.get())
        except Exception as exc:
            messagebox.showerror("First-Time Setup", str(exc), parent=self)
            return
        messagebox.showinfo("First-Time Setup", "Administrator created. Please sign in.", parent=self)
        self.destroy()
        if self.on_complete:
            self.on_complete()
