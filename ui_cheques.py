"""Administrator cheque clearing, bounce, and cancellation workflow."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import DB_PATH
from payment_controls import list_pending_cheques, set_cheque_status
from utils import format_currency, today_str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class ChequeManagementWindow(WorkspacePage):
    """List pending cheques and permit audited terminal status changes."""

    @auth.require_permission("manage_cheques")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Cheque Management")
        self.geometry("980x560")
        self.bank_ref_var = tk.StringVar()
        self.cleared_date_var = tk.StringVar(value=today_str())
        self._build()
        self.refresh()

    def _build(self) -> None:
        columns = ("id", "receipt", "student", "cheque", "bank", "amount", "date", "collector")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=17)
        for key, title, width in (
            ("id", "Payment ID", 75), ("receipt", "Receipt", 130), ("student", "Student", 170),
            ("cheque", "Cheque Number", 130), ("bank", "Bank", 120), ("amount", "Amount", 100),
            ("date", "Received", 90), ("collector", "Collected By", 110),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)
        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(form, text="Cleared Date").pack(side="left")
        ttk.Entry(form, textvariable=self.cleared_date_var, width=12).pack(side="left", padx=5)
        ttk.Label(form, text="Bank Reference").pack(side="left", padx=(12, 0))
        ttk.Entry(form, textvariable=self.bank_ref_var, width=24).pack(side="left", padx=5)
        ttk.Button(form, text="Mark Cleared", command=lambda: self._change("CLEARED")).pack(side="left", padx=4)
        ttk.Button(form, text="Mark Bounced", command=lambda: self._change("BOUNCED")).pack(side="left", padx=4)
        ttk.Button(form, text="Cancel", command=lambda: self._change("CANCELLED")).pack(side="left", padx=4)
        ttk.Button(form, text="Refresh", command=self.refresh).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        with _connect() as conn:
            rows = list_pending_cheques(conn)
        for row in rows:
            self.tree.insert("", "end", iid=str(row["id"]), values=(
                row["id"], row["receipt_no"], row["student"], row["cheque_number"], row["bank"] or "",
                format_currency(row["amount_paid"]), row["payment_date"], row["collected_by"] or "",
            ))

    def _change(self, status: str) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Cheque", "Select a pending cheque.", parent=self)
            return
        if not messagebox.askyesno("Cheque", f"Mark selected cheque as {status}?", parent=self):
            return
        try:
            with _connect() as conn:
                set_cheque_status(
                    conn, int(selection[0]), status, auth.CURRENT_SESSION.user_id,
                    self.cleared_date_var.get(), self.bank_ref_var.get(),
                )
        except (ValueError, sqlite3.IntegrityError) as exc:
            messagebox.showerror("Cheque", str(exc), parent=self)
            return
        self.bank_ref_var.set("")
        self.refresh()
