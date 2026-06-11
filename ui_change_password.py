"""Password-change window for authenticated SFMS users."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

import bcrypt

import auth
from ui_workspace import WorkspacePage
from audit import log_action
from config import APP_TITLE, DB_PATH, SPLASH_BG, SPLASH_FG

PASSWORD_CHANGE_ACTION = "PASSWORD_CHANGE"
USERS_TABLE = "users"
WINDOW_SIZE = "420x300"
PASSWORD_MASK = "*"
MIN_PASSWORD_LENGTH = 8


class ChangePasswordWindow(WorkspacePage):
    """Tkinter window that lets the current user change their password."""

    def __init__(self, master=None, *, embedded: bool = False):
        """Create and display the change-password form."""
        super().__init__(master, embedded=embedded)
        self.current_password_var = tk.StringVar()
        self.new_password_var = tk.StringVar()
        self.confirm_password_var = tk.StringVar()
        self.title(f"{APP_TITLE} - Change Password")
        self.geometry(WINDOW_SIZE)
        self.configure(bg=SPLASH_BG)
        self.resizable(False, False)
        self._center_window()
        self._build_widgets()

    def _center_window(self) -> None:
        """Center the window on screen."""
        self.update_idletasks()
        width = int(WINDOW_SIZE.split("x")[0])
        height = int(WINDOW_SIZE.split("x")[1])
        x_position = (self.winfo_screenwidth() - width) // 2
        y_position = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{WINDOW_SIZE}+{x_position}+{y_position}")

    def _build_widgets(self) -> None:
        """Build password fields and action buttons."""
        tk.Label(self, text="Change Password", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 18, "bold")).pack(pady=(24, 16))
        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(padx=36, fill="x")

        self._add_password_row(form, "Current Password", self.current_password_var, 0)
        self._add_password_row(form, "New Password", self.new_password_var, 2)
        self._add_password_row(form, "Confirm New Password", self.confirm_password_var, 4)

        button_frame = tk.Frame(form, bg=SPLASH_BG)
        button_frame.grid(row=6, column=0, pady=(18, 0), sticky="ew")
        ttk.Button(button_frame, text="Update Password", command=self._on_update_click).pack(side="left", expand=True, fill="x")
        ttk.Button(button_frame, text="Cancel", command=self._on_cancel_click).pack(side="left", padx=(8, 0), expand=True, fill="x")

    def _add_password_row(self, parent: tk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        """Add a labeled password entry row to the form."""
        tk.Label(parent, text=label, bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, show=PASSWORD_MASK).grid(row=row + 1, column=0, sticky="ew", pady=(2, 8))
        parent.columnconfigure(0, weight=1)

    def _on_cancel_click(self) -> None:
        """Close the change-password window after touching the active session."""
        auth.touch_session()
        self.destroy()

    def _on_update_click(self) -> None:
        """Validate and save the new password for the current user."""
        auth.touch_session()
        if not auth.current_user_can_write():
            messagebox.showerror("Not logged in", "Please log in before changing your password.")
            return
        current_password = self.current_password_var.get()
        new_password = self.new_password_var.get()
        confirm_password = self.confirm_password_var.get()
        if len(new_password) < MIN_PASSWORD_LENGTH:
            messagebox.showerror("Invalid password", "New password must be at least 8 characters long.")
            return
        if new_password != confirm_password:
            messagebox.showerror("Invalid password", "New password and confirmation do not match.")
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (auth.CURRENT_SESSION.user_id,)).fetchone()
            if row is None or not bcrypt.checkpw(current_password.encode("utf-8"), row[0].encode("utf-8")):
                messagebox.showerror("Invalid password", "Current password is incorrect.")
                return
            new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, auth.CURRENT_SESSION.user_id))
            log_action(conn, auth.CURRENT_SESSION.user_id, PASSWORD_CHANGE_ACTION, USERS_TABLE, auth.CURRENT_SESSION.user_id)
        messagebox.showinfo("Password changed", "Your password has been changed successfully.")
        self.destroy()
