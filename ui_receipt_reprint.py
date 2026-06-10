"""Receipt search and administrator-controlled duplicate printing."""

from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from audit import log_action
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from receipt_printer import print_receipt
from utils import format_currency, now_str


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for receipt searches and reprints."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class ReprintWindow(tk.Toplevel):
    """Search receipt history and generate a reason-backed duplicate receipt."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the receipt search and reprint interface."""
        super().__init__(master)
        self.title("Receipt Reprint")
        self.geometry("900x600")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.selected_receipt_no: str | None = None
        self._build_widgets()

    def _build_widgets(self) -> None:
        """Build search controls, receipt list, details, and reprint reason."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=12)
        tk.Label(top, text="Receipt No. or Student", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(top, textvariable=self.search_var, width=38).pack(side="left", padx=8)
        ttk.Button(top, text="Search", command=self.search).pack(side="left")

        columns = ("receipt_no", "student", "date", "total", "type", "reprints")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=9)
        for column, heading, width in (
            ("receipt_no", "Receipt No.", 170), ("student", "Student", 210),
            ("date", "Date", 110), ("total", "Total", 110),
            ("type", "Type", 80), ("reprints", "Reprints", 80),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.pack(fill="x", padx=12)
        self.tree.bind("<<TreeviewSelect>>", self.load_details)

        details_frame = tk.LabelFrame(self, text="Receipt Details", bg=SPLASH_BG, fg=SPLASH_FG, padx=10, pady=8)
        details_frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.details = tk.Text(details_frame, height=10, wrap="word", state="disabled", font=("Courier New", 10))
        self.details.pack(fill="both", expand=True)

        bottom = tk.Frame(self, bg=SPLASH_BG)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(bottom, text="Reprint Reason", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(bottom, textvariable=self.reason_var, width=54).pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(bottom, text="Reprint Receipt", command=self.reprint).pack(side="right")

    def search(self) -> None:
        """Search by exact/partial receipt number or partial student name."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT r.receipt_no, s.name AS student, r.total_paid, r.receipt_type,
                       r.reprint_count, MIN(p.payment_date) AS payment_date
                FROM receipts r
                JOIN students s ON s.id = r.student_id
                LEFT JOIN payments p ON p.receipt_no = r.receipt_no
                WHERE r.receipt_no LIKE ? OR s.name LIKE ?
                GROUP BY r.receipt_no
                ORDER BY r.id DESC
                """,
                (term, term),
            ).fetchall()
        for row in rows:
            self.tree.insert("", "end", iid=row["receipt_no"], values=(row["receipt_no"], row["student"], row["payment_date"] or "", format_currency(row["total_paid"] or 0), row["receipt_type"] or "", row["reprint_count"] or 0))

    def load_details(self, _event=None) -> None:
        """Display student, payment date, total, fee heads, and mode details."""
        auth.touch_session()
        selected = self.tree.selection()
        if not selected:
            return
        self.selected_receipt_no = selected[0]
        with _connect() as conn:
            receipt = conn.execute(
                """
                SELECT r.receipt_no, r.total_paid, r.receipt_type, r.reprint_count,
                       s.name, s.class, s.section
                FROM receipts r JOIN students s ON s.id = r.student_id
                WHERE r.receipt_no = ?
                """,
                (self.selected_receipt_no,),
            ).fetchone()
            payments = conn.execute(
                """
                SELECT fh.name AS fee_head, COALESCE(l.original_amount,p.amount_due) AS amount_due,
                       p.amount_paid, COALESCE(l.balance,0) AS balance,
                       p.payment_date, p.payment_mode, p.note
                FROM payments p
                LEFT JOIN payment_allocations pa ON pa.payment_id=p.id
                LEFT JOIN charge_ledger l ON l.charge_id=pa.charge_id
                LEFT JOIN fee_heads fh ON fh.id = p.fee_head_id
                WHERE p.receipt_no = ? ORDER BY p.id
                """,
                (self.selected_receipt_no,),
            ).fetchall()
        section_text = f" - {receipt['section']}" if receipt["section"] else ""
        class_text = f"{receipt['class'] or ''}{section_text}"
        lines = [
            f"Receipt: {receipt['receipt_no']}",
            f"Student: {receipt['name']}",
            f"Class: {class_text}",
            f"Date: {payments[0]['payment_date'] if payments else ''}",
            f"Total: {format_currency(receipt['total_paid'] or 0)}",
            f"Type: {receipt['receipt_type'] or ''}",
            f"Previous Reprints: {receipt['reprint_count'] or 0}",
            "",
            "Fee Heads:",
        ]
        for payment in payments:
            lines.append(f"  {payment['fee_head'] or 'Fee'}: due {format_currency(payment['amount_due'] or 0)}, paid {format_currency(payment['amount_paid'] or 0)}, balance {format_currency(payment['balance'] or 0)} [{payment['payment_mode'] or ''}]")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(lines))
        self.details.configure(state="disabled")

    @auth.require_role("ADMIN")
    def reprint(self) -> None:
        """Require a reason, print a duplicate, and audit the stated reason."""
        auth.touch_session()
        reason = self.reason_var.get().strip()
        if not self.selected_receipt_no:
            messagebox.showerror("Receipt reprint", "Select a receipt first.", parent=self)
            return
        if not reason:
            messagebox.showerror("Receipt reprint", "A reprint reason is mandatory.", parent=self)
            return
        if not messagebox.askyesno("Receipt reprint", f"Print duplicate receipt {self.selected_receipt_no}?", parent=self):
            return
        try:
            with _connect() as conn:
                path = print_receipt(conn, self.selected_receipt_no, reprint=True)
                log_action(
                    conn,
                    auth.CURRENT_SESSION.user_id,
                    "RECEIPT_REPRINT",
                    "receipts",
                    self.selected_receipt_no,
                    None,
                    json.dumps({"reason": reason, "reprinted_at": now_str()}, default=str),
                )
        except Exception as exc:
            messagebox.showerror("Receipt reprint", str(exc), parent=self)
            return
        self.reason_var.set("")
        self.search()
        messagebox.showinfo("Receipt reprint", f"Duplicate receipt saved to:\n{path}", parent=self)
