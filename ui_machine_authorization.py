"""Owner authorization screen for trusting a changed machine fingerprint."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from ui_theme import apply_theme

from config import DB_PATH
from integrity import authorize_new_machine
from security_utils import MACHINE_AUTHORIZATION_REQUIRED_MESSAGE


class MachineAuthorizationWindow(tk.Toplevel):
    """Require an admin password before trusting a new machine fingerprint."""

    def __init__(self, master=None, on_complete=None):
        if master is None and tk._default_root is None:
            master = tk.Tk()
            master.withdraw()
        super().__init__(master)
        apply_theme(self)
        self.on_complete = on_complete
        self.title("Owner Authorization Required")
        self.geometry("520x280")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Owner Authorization Required", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text=MACHINE_AUTHORIZATION_REQUIRED_MESSAGE, wraplength=450).pack(anchor="w", pady=(0, 16))
        ttk.Label(frame, text="Admin username").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.username, width=42).pack(anchor="w", pady=(2, 10))
        ttk.Label(frame, text="Admin password").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.password, show="*", width=42).pack(anchor="w", pady=(2, 14))
        ttk.Button(frame, text="Authorize This Machine", command=self._authorize).pack(fill="x")

    def _authorize(self) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            ok = authorize_new_machine(conn, self.username.get(), self.password.get())
        if not ok:
            messagebox.showerror("Machine Authorization", "Invalid administrator credentials.", parent=self)
            return
        messagebox.showinfo("Machine Authorization", "This machine is now authorized.", parent=self)
        self.destroy()
        if self.on_complete:
            self.on_complete()
