"""Fee-head master-data management screen for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ui_master_utils import audit, connect_db, ensure_admin_write

REGISTER_TYPES = ("BIG", "SMALL", "BOTH")


class FeeHeadsWindow(tk.Toplevel):
    """Admin-only fee-head management window."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the fee-head window."""
        super().__init__(master)
        self.title("Fee Heads")
        self.geometry("620x420")
        self.configure(bg=SPLASH_BG)
        self.name_var = tk.StringVar()
        self.register_type_var = tk.StringVar(value=REGISTER_TYPES[0])
        self._build_widgets()
        self.refresh()

    def _build_widgets(self) -> None:
        """Build tree and action controls."""
        columns = ("id", "name", "register_type", "status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for column, heading, width in (
            ("id", "ID", 70), ("name", "Fee Head Name", 250),
            ("register_type", "Register Type", 140), ("status", "Status", 100),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)

        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(form, text="Name", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(form, textvariable=self.name_var, width=28).pack(side="left", padx=6)
        tk.Label(form, text="Register", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(10, 0))
        ttk.Combobox(form, textvariable=self.register_type_var, values=REGISTER_TYPES, state="readonly", width=10).pack(side="left", padx=6)
        ttk.Button(form, text="Add", command=self.add_fee_head).pack(side="left", padx=6)
        ttk.Button(form, text="Deactivate", command=self.deactivate_selected).pack(side="left", padx=6)

    def refresh(self) -> None:
        """Reload fee heads from the database."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        with connect_db() as conn:
            rows = conn.execute("SELECT id, name, register_type, is_active FROM fee_heads ORDER BY name").fetchall()
        for row in rows:
            status = "Active" if row["is_active"] else "Inactive"
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["name"], row["register_type"], status))

    @auth.require_role("ADMIN")
    def add_fee_head(self) -> None:
        """Insert a new fee head and audit it."""
        if not ensure_admin_write():
            return
        name = self.name_var.get().strip()
        register_type = self.register_type_var.get()
        if not name:
            messagebox.showerror("Validation", "Fee head name is required.")
            return
        with connect_db() as conn:
            cursor = conn.execute("INSERT INTO fee_heads (name, register_type, is_active) VALUES (?, ?, 1)", (name, register_type))
            audit(conn, "FEE_HEAD_ADD", "fee_heads", cursor.lastrowid, None, {"name": name, "register_type": register_type})
        self.name_var.set("")
        self.refresh()

    def _selected_id(self) -> int | None:
        """Return selected fee-head id if a row is selected."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Select fee head", "Please select a fee head first.")
            return None
        return int(selected[0])

    @auth.require_role("ADMIN")
    def deactivate_selected(self) -> None:
        """Deactivate a fee head if no payments reference it."""
        fee_head_id = self._selected_id()
        if fee_head_id is None or not ensure_admin_write():
            return
        with connect_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM payments WHERE fee_head_id = ?", (fee_head_id,)).fetchone()[0]
            if count:
                messagebox.showerror("Cannot deactivate", "Payments already exist for this fee head.")
                return
            old = dict(conn.execute("SELECT * FROM fee_heads WHERE id = ?", (fee_head_id,)).fetchone())
            conn.execute("UPDATE fee_heads SET is_active = 0 WHERE id = ?", (fee_head_id,))
            audit(conn, "FEE_HEAD_DEACTIVATE", "fee_heads", fee_head_id, old, {"is_active": 0})
        self.refresh()
