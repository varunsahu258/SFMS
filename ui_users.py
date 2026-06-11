"""Administrator user-account management for SFMS."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import bcrypt

import auth
from ui_workspace import WorkspacePage
from audit import log_action
from config import DB_PATH
from ui_theme import apply_theme

MIN_PASSWORD_LENGTH = 8
FORCE_CHANGE_PREFIX = "force_password_change_user_"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


class UserManagementWindow(WorkspacePage):
    """Create, activate, reset, and unlock accountant accounts."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("User Management")
        self.geometry("850x520")
        self.transient(master)
        self._build()
        self.refresh()

    def _build(self) -> None:
        columns = ("id", "username", "role", "status", "failed", "locked")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=16)
        for key, title, width in (
            ("id", "ID", 55), ("username", "Username", 190), ("role", "Role", 130),
            ("status", "Status", 100), ("failed", "Failed Attempts", 120), ("locked", "Locked", 190),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center" if key != "username" else "w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        for text, command in (
            ("Add Accountant", self.add_accountant), ("Deactivate / Reactivate", self.toggle_status),
            ("Reset Password", self.reset_password), ("Unlock Account", self.unlock_account),
            ("Refresh", self.refresh),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=4)

    def _selected(self) -> sqlite3.Row | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showerror("User Management", "Select a user.", parent=self)
            return None
        user_id = int(self.tree.item(selection[0], "values")[0])
        with _connect() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def refresh(self) -> None:
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        with _connect() as conn:
            rows = conn.execute("SELECT id, username, role, is_active, failed_attempts, locked_at FROM users ORDER BY username").fetchall()
        for row in rows:
            self.tree.insert("", "end", values=(
                row["id"], row["username"], row["role"], "Active" if row["is_active"] else "Inactive",
                row["failed_attempts"], row["locked_at"] or "No",
            ))

    def add_accountant(self) -> None:
        auth.touch_session()
        username = simpledialog.askstring("Add Accountant", "Username:", parent=self)
        if not username:
            return
        password = simpledialog.askstring("Add Accountant", "Temporary password (minimum 8 characters):", show="*", parent=self)
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            messagebox.showerror("Add Accountant", "Password must be at least 8 characters.", parent=self)
            return
        try:
            with _connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, role, is_active, failed_attempts) VALUES (?, ?, 'ACCOUNTANT', 1, 0)",
                    (username.strip(), _password_hash(password)),
                )
                user_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value='1'",
                    (f"{FORCE_CHANGE_PREFIX}{user_id}",),
                )
                log_action(conn, auth.CURRENT_SESSION.user_id, "USER_CREATED", "users", user_id, None, f"username={username};role=ACCOUNTANT")
        except sqlite3.IntegrityError:
            messagebox.showerror("Add Accountant", "That username already exists.", parent=self)
            return
        self.refresh()
        messagebox.showinfo("Add Accountant", "Account created. The temporary password must be changed at first login.", parent=self)

    def toggle_status(self) -> None:
        auth.touch_session()
        row = self._selected()
        if row is None:
            return
        if auth.CURRENT_SESSION and row["id"] == auth.CURRENT_SESSION.user_id:
            messagebox.showerror("User Management", "You cannot deactivate your own account.", parent=self)
            return
        new_status = 0 if row["is_active"] else 1
        with _connect() as conn:
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, row["id"]))
            log_action(conn, auth.CURRENT_SESSION.user_id, "USER_STATUS_CHANGED", "users", row["id"], str(row["is_active"]), str(new_status))
        self.refresh()

    def reset_password(self) -> None:
        auth.touch_session()
        row = self._selected()
        if row is None:
            return
        password = simpledialog.askstring("Reset Password", "New temporary password:", show="*", parent=self)
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            messagebox.showerror("Reset Password", "Password must be at least 8 characters.", parent=self)
            return
        with _connect() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_password_hash(password), row["id"]))
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value='1'",
                (f"{FORCE_CHANGE_PREFIX}{row['id']}",),
            )
            log_action(conn, auth.CURRENT_SESSION.user_id, "PASSWORD_RESET", "users", row["id"], None, "temporary password set")
        messagebox.showinfo("Reset Password", "Password reset. The user must change it at next login.", parent=self)

    def unlock_account(self) -> None:
        auth.touch_session()
        row = self._selected()
        if row is None:
            return
        with _connect() as conn:
            conn.execute("UPDATE users SET failed_attempts=0, locked_at=NULL WHERE id=?", (row["id"],))
            log_action(conn, auth.CURRENT_SESSION.user_id, "ACCOUNT_UNLOCKED", "users", row["id"], None, "failed_attempts=0;locked_at=NULL")
        self.refresh()


class MandatoryPasswordChangeDialog(tk.Toplevel):
    """Non-skippable first-login password change for temporary accounts."""

    def __init__(self, master, on_complete):
        super().__init__(master)
        apply_theme(self)
        self.on_complete = on_complete
        self.title("Change Temporary Password")
        self.geometry("430x260")
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()
        ttk.Label(self, text="You must replace your temporary password.", font=("Segoe UI", 12, "bold")).pack(pady=18)
        form = ttk.Frame(self)
        form.pack(fill="x", padx=35)
        ttk.Label(form, text="New password").pack(anchor="w")
        ttk.Entry(form, textvariable=self.password, show="*").pack(fill="x", pady=(2, 10))
        ttk.Label(form, text="Confirm password").pack(anchor="w")
        ttk.Entry(form, textvariable=self.confirm, show="*").pack(fill="x", pady=(2, 12))
        ttk.Button(form, text="Save and Continue", command=self._save).pack(fill="x")

    def _save(self) -> None:
        password = self.password.get()
        if len(password) < MIN_PASSWORD_LENGTH or password != self.confirm.get():
            messagebox.showerror("Password", "Passwords must match and contain at least 8 characters.", parent=self)
            return
        user_id = auth.CURRENT_SESSION.user_id
        with _connect() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_password_hash(password), user_id))
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,'0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'",
                (f"{FORCE_CHANGE_PREFIX}{user_id}",),
            )
            log_action(conn, user_id, "PASSWORD_CHANGE", "users", user_id, None, "first-login password changed")
        self.grab_release()
        self.destroy()
        self.on_complete()
