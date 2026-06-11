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
        self.geometry("470x320")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.username = tk.StringVar(value=DEFAULT_ADMIN_USERNAME)
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="First-Time Setup", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="Create the owner administrator account before using SFMS.", wraplength=400).pack(anchor="w", pady=(0, 16))
        for label, variable, show in (
            ("Administrator username", self.username, ""),
            ("Password", self.password, "*"),
            ("Confirm password", self.confirm, "*"),
        ):
            ttk.Label(frame, text=label).pack(anchor="w")
            ttk.Entry(frame, textvariable=variable, show=show, width=42).pack(anchor="w", pady=(2, 10))
        ttk.Button(frame, text="Create Admin", command=self._create).pack(fill="x", pady=(8, 0))

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
