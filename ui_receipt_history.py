"""Read-only receipt history search for authorized operational users."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import auth
from ui_master_utils import connect_db
from ui_workspace import WorkspacePage
from utils import format_currency


class ReceiptHistoryWindow(WorkspacePage):
    """Search and inspect past receipts without granting reprint authority."""

    @auth.require_permission("view_receipts")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Receipt History")
        self.geometry("1080x650")
        self.search_var = tk.StringVar()
        self.register_var = tk.StringVar(value="All Registers")
        self._build_widgets()
        self.search()

    def _build_widgets(self) -> None:
        page = ttk.Frame(self, padding=22); page.pack(fill="both", expand=True)
        ttk.Label(page, text="Receipt History", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Search by receipt number, student, scholar number, or payment date.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        filters = ttk.Frame(page); filters.pack(fill="x")
        entry = ttk.Entry(filters, textvariable=self.search_var, width=42); entry.pack(side="left")
        entry.bind("<Return>", lambda _event: self.search())
        ttk.Combobox(filters, textvariable=self.register_var,
                     values=("All Registers", "Main Register", "Small Register"),
                     state="readonly", width=18).pack(side="left", padx=8)
        ttk.Button(filters, text="Search", command=self.search, style="Accent.TButton").pack(side="left")
        columns = ("receipt", "student", "scholar", "date", "amount", "register", "mode", "collector")
        self.tree = ttk.Treeview(page, columns=columns, show="headings", height=11)
        for col, title, width in (("receipt", "Receipt No.", 155), ("student", "Student", 190),
                                  ("scholar", "Scholar No.", 100), ("date", "Date", 100),
                                  ("amount", "Amount", 100), ("register", "Register", 90),
                                  ("mode", "Mode", 90), ("collector", "Collected By", 110)):
            self.tree.heading(col, text=title); self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="x", pady=(12, 8)); self.tree.bind("<<TreeviewSelect>>", self.load_details)
        self.details = tk.Text(page, height=12, wrap="word", state="disabled")
        self.details.pack(fill="both", expand=True)

    def search(self) -> None:
        auth.touch_session()
        for item in self.tree.get_children(): self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        register = self.register_var.get()
        receipt_type = "SMALL" if register == "Small Register" else "BIG" if register == "Main Register" else ""
        with connect_db() as conn:
            rows = conn.execute(
                """SELECT r.receipt_no,r.total_paid,r.receipt_type,s.name,s.scholar_no,
                          MIN(p.payment_date) payment_date,GROUP_CONCAT(DISTINCT p.payment_mode) modes,
                          GROUP_CONCAT(DISTINCT u.username) collectors
                   FROM receipts r JOIN students s ON s.id=r.student_id
                   LEFT JOIN payments p ON p.receipt_no=r.receipt_no
                   LEFT JOIN users u ON u.id=p.collected_by
                   WHERE (r.receipt_no LIKE ? OR s.name LIKE ? OR s.scholar_no LIKE ? OR p.payment_date LIKE ?)
                     AND (?='' OR UPPER(r.receipt_type)=?)
                   GROUP BY r.id ORDER BY r.id DESC LIMIT 500""",
                (term, term, term, term, receipt_type, receipt_type),
            ).fetchall()
        for row in rows:
            label = "Small" if str(row["receipt_type"] or "").upper() == "SMALL" else "Main"
            self.tree.insert("", "end", iid=row["receipt_no"], values=(row["receipt_no"], row["name"],
                row["scholar_no"] or "", row["payment_date"] or "", format_currency(row["total_paid"] or 0),
                label, row["modes"] or "", row["collectors"] or ""))

    def load_details(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected: return
        with connect_db() as conn:
            rows = conn.execute(
                """SELECT fh.name,p.amount_paid,p.payment_mode,p.payment_date,p.cheque_number,p.upi_reference
                   FROM payments p LEFT JOIN fee_heads fh ON fh.id=p.fee_head_id
                   WHERE p.receipt_no=? ORDER BY p.id""", (selected[0],),
            ).fetchall()
        lines = [f"Receipt: {selected[0]}", ""]
        lines.extend(f"{row['name'] or 'Fee'}: {format_currency(row['amount_paid'])} | {row['payment_mode']} | {row['payment_date']}"
                     for row in rows)
        self.details.configure(state="normal"); self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(lines)); self.details.configure(state="disabled")
