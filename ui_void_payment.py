"""Administrator-only immutable payment void workflow for SFMS."""

from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from audit import log_action
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from utils import compute_hash, format_currency, generate_receipt_no, now_str, today_str


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for void-payment operations."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_void_receipt(
    conn: sqlite3.Connection,
    original_receipt: dict,
    original_payments: list[dict],
    original_receipt_no: str,
    reason: str,
    user_id: int,
) -> str:
    """Append immutable reversal rows and return the new void receipt number."""
    duplicate = conn.execute(
        "SELECT 1 FROM payments WHERE note = ? LIMIT 1",
        (f"VOID of {original_receipt_no}",),
    ).fetchone()
    if duplicate:
        raise ValueError("This receipt has already been voided.")

    payment_date = today_str()
    void_receipt_no = generate_receipt_no(conn)
    total_voided = 0.0
    void_payment_ids: list[int] = []
    for original in original_payments:
        reversed_amount = -float(original["amount_paid"] or 0)
        payment_hash = compute_hash(
            void_receipt_no,
            original["student_id"],
            reversed_amount,
            payment_date,
        )
        cursor = conn.execute(
            """
            INSERT INTO payments (
                student_id, receipt_no, fee_head_id, amount_due,
                amount_paid, balance, payment_date, collected_by,
                payment_mode, note, hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                original["student_id"], void_receipt_no,
                original["fee_head_id"], original["amount_due"],
                reversed_amount, original["balance"], payment_date,
                user_id, original["payment_mode"],
                f"VOID of {original_receipt_no}", payment_hash,
            ),
        )
        void_payment_ids.append(cursor.lastrowid)
        total_voided += reversed_amount

    conn.execute(
        """
        INSERT INTO receipts (
            receipt_no, student_id, total_paid, receipt_type,
            printed_at, printed_by, reprint_count
        ) VALUES (?, ?, ?, 'VOID', ?, ?, 0)
        """,
        (void_receipt_no, original_receipt["student_id"], total_voided, now_str(), user_id),
    )
    receipt_hash = compute_hash(
        void_receipt_no, original_receipt["student_id"], total_voided, payment_date
    )
    conn.execute(
        "INSERT INTO receipt_hashes (receipt_no, sha256_hash, created_at) VALUES (?, ?, ?)",
        (void_receipt_no, receipt_hash, now_str()),
    )
    log_action(
        conn,
        user_id,
        "PAYMENT_VOID",
        "payments",
        void_receipt_no,
        None,
        json.dumps(
            {
                "reason": reason,
                "original_receipt_no": original_receipt_no,
                "void_receipt_no": void_receipt_no,
                "void_payment_ids": void_payment_ids,
                "total_voided": total_voided,
            },
            default=str,
        ),
    )
    return void_receipt_no


class VoidPaymentWindow(tk.Toplevel):
    """Create reversal rows without modifying immutable original payments."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the receipt search and payment-void interface."""
        super().__init__(master)
        self.title("Void Payment")
        self.geometry("980x610")
        self.configure(bg=SPLASH_BG)
        self.receipt_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.original_receipt_no: str | None = None
        self.original_receipt: dict | None = None
        self.original_payments: list[dict] = []
        self._build_widgets()

    def _build_widgets(self) -> None:
        """Build receipt search, immutable payment details, and reason controls."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=12)
        tk.Label(top, text="Receipt No.", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(top, textvariable=self.receipt_var, width=34).pack(side="left", padx=8)
        ttk.Button(top, text="Search", command=self.search).pack(side="left")

        self.summary_var = tk.StringVar(value="Search for a receipt to review its immutable payment rows.")
        tk.Label(self, textvariable=self.summary_var, bg=SPLASH_BG, fg=SPLASH_FG, anchor="w").pack(fill="x", padx=12, pady=(0, 8))

        columns = ("fee_head", "amount_due", "amount_paid", "balance", "date", "mode", "note")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=15)
        for column, heading, width in (
            ("fee_head", "Fee Head", 180), ("amount_due", "Amount Due", 110),
            ("amount_paid", "Amount Paid", 110), ("balance", "Balance", 110),
            ("date", "Payment Date", 105), ("mode", "Mode", 85), ("note", "Note", 180),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        bottom = tk.Frame(self, bg=SPLASH_BG)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(bottom, text="Void Reason", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(bottom, textvariable=self.reason_var, width=58).pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(bottom, text="Confirm Void", command=self.confirm_void).pack(side="right")

    def _clear_details(self) -> None:
        """Clear the loaded receipt state and payment tree."""
        self.original_receipt_no = None
        self.original_receipt = None
        self.original_payments = []
        for item in self.tree.get_children():
            self.tree.delete(item)

    def search(self) -> None:
        """Load one original receipt and all of its payment rows."""
        auth.touch_session()
        self._clear_details()
        receipt_no = self.receipt_var.get().strip()
        if not receipt_no:
            messagebox.showerror("Void payment", "Enter a receipt number.", parent=self)
            return
        with _connect() as conn:
            receipt = conn.execute(
                """
                SELECT r.*, s.name AS student_name, s.class AS student_class,
                       s.section AS student_section
                FROM receipts r JOIN students s ON s.id = r.student_id
                WHERE r.receipt_no = ?
                """,
                (receipt_no,),
            ).fetchone()
            if receipt is None:
                messagebox.showerror("Void payment", "Receipt was not found.", parent=self)
                return
            if str(receipt["receipt_type"] or "").upper() == "VOID":
                messagebox.showerror("Void payment", "A void receipt cannot itself be voided.", parent=self)
                return
            already_voided = conn.execute(
                "SELECT 1 FROM payments WHERE note = ? LIMIT 1",
                (f"VOID of {receipt_no}",),
            ).fetchone()
            if already_voided:
                messagebox.showerror("Void payment", "This receipt has already been voided.", parent=self)
                return
            payments = [dict(row) for row in conn.execute(
                """
                SELECT p.*, fh.name AS fee_head
                FROM payments p LEFT JOIN fee_heads fh ON fh.id = p.fee_head_id
                WHERE p.receipt_no = ? ORDER BY p.id
                """,
                (receipt_no,),
            )]
        if not payments:
            messagebox.showerror("Void payment", "Receipt has no payment rows.", parent=self)
            return

        self.original_receipt_no = receipt_no
        self.original_receipt = dict(receipt)
        self.original_payments = payments
        section = f" - {receipt['student_section']}" if receipt["student_section"] else ""
        self.summary_var.set(
            f"{receipt['student_name']} | {receipt['student_class'] or ''}{section} | "
            f"Receipt total: {format_currency(receipt['total_paid'] or 0)}"
        )
        for payment in payments:
            self.tree.insert(
                "",
                "end",
                values=(
                    payment["fee_head"] or "Fee",
                    format_currency(payment["amount_due"] or 0),
                    format_currency(payment["amount_paid"] or 0),
                    format_currency(payment["balance"] or 0),
                    payment["payment_date"] or "",
                    payment["payment_mode"] or "",
                    payment["note"] or "",
                ),
            )

    @auth.require_role("ADMIN")
    def confirm_void(self) -> None:
        """Append reversing payments, a void receipt, its hash, and an audit record."""
        auth.touch_session()
        reason = self.reason_var.get().strip()
        if not self.original_receipt_no or not self.original_receipt or not self.original_payments:
            messagebox.showerror("Void payment", "Search and load a receipt first.", parent=self)
            return
        if not reason:
            messagebox.showerror("Void payment", "A void reason is mandatory.", parent=self)
            return
        if not messagebox.askyesno(
            "Confirm payment void",
            f"Create an immutable reversal for {self.original_receipt_no}?",
            parent=self,
        ):
            return

        user_id = auth.CURRENT_SESSION.user_id
        try:
            with _connect() as conn:
                void_receipt_no = create_void_receipt(
                    conn,
                    self.original_receipt,
                    self.original_payments,
                    self.original_receipt_no,
                    reason,
                    user_id,
                )
        except (sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Void payment", str(exc), parent=self)
            return

        original_receipt_no = self.original_receipt_no
        self.reason_var.set("")
        self._clear_details()
        self.summary_var.set(f"Receipt {original_receipt_no} voided as {void_receipt_no}.")
        messagebox.showinfo(
            "Void payment",
            f"Void completed. New receipt: {void_receipt_no}",
            parent=self,
        )
