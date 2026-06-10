"""Administrator UI for configuring per-accountant application permissions."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from permissions import DEFAULT_ACCOUNTANT_PERMISSIONS, PERMISSIONS
from ui_master_utils import audit, connect_db
from utils import now_str


class AccountantPermissionsWindow(tk.Toplevel):
    """Allow administrators to grant or revoke delegable accountant capabilities."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        super().__init__(master)
        self.title("Accountant Permissions")
        self.geometry("760x720")
        self.configure(bg=SPLASH_BG)
        self.accountant_var = tk.StringVar()
        self.accountants: dict[str, int] = {}
        self.permission_vars = {item.key: tk.BooleanVar() for item in PERMISSIONS}
        self._build_widgets()
        self._load_accountants()

    def _build_widgets(self) -> None:
        tk.Label(
            self, text="Accountant Permissions", bg=SPLASH_BG, fg=SPLASH_FG,
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(18, 4))
        tk.Label(
            self,
            text=("Select an accountant and choose exactly what that account may access. "
                  "User management, permission management, backup/restore, and application settings "
                  "always remain administrator-only."),
            bg=SPLASH_BG, fg=SPLASH_FG, wraplength=700, justify="left",
        ).pack(fill="x", padx=24, pady=(0, 14))

        selector = tk.Frame(self, bg=SPLASH_BG)
        selector.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(selector, text="Accountant", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        self.accountant_combo = ttk.Combobox(
            selector, textvariable=self.accountant_var, state="readonly", width=34
        )
        self.accountant_combo.pack(side="left", padx=10)
        self.accountant_combo.bind("<<ComboboxSelected>>", self.load_permissions)

        canvas = tk.Canvas(self, bg=SPLASH_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 12))
        canvas.pack(fill="both", expand=True, padx=(24, 0))
        form = tk.Frame(canvas, bg=SPLASH_BG)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        current_category = None
        for item in PERMISSIONS:
            if item.category != current_category:
                current_category = item.category
                tk.Label(
                    form, text=current_category, bg=SPLASH_BG, fg=SPLASH_FG,
                    font=("Segoe UI", 11, "bold"), anchor="w",
                ).pack(fill="x", pady=(12, 3))
            ttk.Checkbutton(form, text=item.label, variable=self.permission_vars[item.key]).pack(
                fill="x", padx=12, pady=2, anchor="w"
            )

        buttons = tk.Frame(self, bg=SPLASH_BG)
        buttons.pack(fill="x", padx=24, pady=16)
        ttk.Button(buttons, text="Select Default", command=self.select_defaults).pack(side="left")
        ttk.Button(buttons, text="Clear All", command=self.clear_all).pack(side="left", padx=8)
        ttk.Button(buttons, text="Save Permissions", command=self.save_permissions).pack(side="right")

    def _load_accountants(self) -> None:
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT id,username,is_active FROM users WHERE role='ACCOUNTANT' ORDER BY username"
            ).fetchall()
        self.accountants = {
            f"{row['username']} ({'Active' if row['is_active'] else 'Inactive'})": int(row["id"])
            for row in rows
        }
        values = list(self.accountants)
        self.accountant_combo.configure(values=values)
        if values:
            self.accountant_var.set(values[0])
            self.load_permissions()
        else:
            self.accountant_var.set("")
            self.clear_all()

    def _selected_user_id(self) -> int | None:
        return self.accountants.get(self.accountant_var.get())

    def load_permissions(self, _event=None) -> None:
        user_id = self._selected_user_id()
        if user_id is None:
            self.clear_all()
            return
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT permission_key,allowed FROM user_permissions WHERE user_id=?", (user_id,)
            ).fetchall()
        overrides = {row["permission_key"]: bool(row["allowed"]) for row in rows}
        for item in PERMISSIONS:
            self.permission_vars[item.key].set(
                overrides.get(item.key, item.key in DEFAULT_ACCOUNTANT_PERMISSIONS)
            )

    def select_defaults(self) -> None:
        for key, variable in self.permission_vars.items():
            variable.set(key in DEFAULT_ACCOUNTANT_PERMISSIONS)

    def clear_all(self) -> None:
        for variable in self.permission_vars.values():
            variable.set(False)

    @auth.require_role("ADMIN")
    def save_permissions(self) -> None:
        user_id = self._selected_user_id()
        if user_id is None:
            messagebox.showerror("Accountant Permissions", "Create or select an accountant first.", parent=self)
            return
        new_values = {key: bool(variable.get()) for key, variable in self.permission_vars.items()}
        with connect_db() as conn:
            old_rows = conn.execute(
                "SELECT permission_key,allowed FROM user_permissions WHERE user_id=?", (user_id,)
            ).fetchall()
            old_values = {row["permission_key"]: bool(row["allowed"]) for row in old_rows}
            conn.executemany(
                """INSERT INTO user_permissions(user_id,permission_key,allowed,updated_at,updated_by)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id,permission_key) DO UPDATE SET
                       allowed=excluded.allowed,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                [
                    (user_id, key, int(allowed), now_str(), auth.CURRENT_SESSION.user_id)
                    for key, allowed in new_values.items()
                ],
            )
            audit(conn, "ACCOUNTANT_PERMISSIONS_UPDATE", "user_permissions", user_id, old_values, new_values)
        messagebox.showinfo("Accountant Permissions", "Permissions saved. They apply on the accountant’s next dashboard login.", parent=self)
