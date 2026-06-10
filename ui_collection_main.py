"""Main BIG-register collection using one overall payment amount."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, charge_rows
from money import max_payment_amount, validate_payment_amount
from ui_collection_common import (
    MODE_LABELS,
    MODE_TO_DB,
    CollectionBaseWindow,
    connect_db,
)
from utils import format_currency


def main_collection_summary(conn, student_id: int) -> dict:
    """Return overall BIG-register dues and allocation order for one student."""
    rows = [
        dict(row) for row in charge_rows(conn, student_id, active_academic_year(conn), ("BIG", "BOTH"))
        if float(row["balance"] or 0) > 0
    ]
    if not rows:
        return {"total_due": 0.0, "oldest_due_date": "", "charges": [], "tuition_fee_head_id": None}
    def due_key(row):
        value = str(row.get("due_date") or "")
        for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, date_format), int(row["charge_id"])
            except ValueError:
                continue
        return datetime.max, int(row["charge_id"])

    rows.sort(key=due_key)
    tuition = conn.execute(
        "SELECT id FROM fee_heads WHERE is_active=1 AND LOWER(TRIM(name)) LIKE '%tuition%' ORDER BY id LIMIT 1"
    ).fetchone()
    if tuition is None:
        raise ValueError("Create an active Tuition Fee head before using Main Collection.")
    return {
        "total_due": sum(float(row["balance"] or 0) for row in rows),
        "oldest_due_date": str(rows[0].get("due_date") or ""),
        "charges": rows,
        "tuition_fee_head_id": int(tuition[0]),
    }


class CollectionMainWindow(CollectionBaseWindow):
    """Collect one amount and record it under Tuition Fee while reducing overall dues."""

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"

    @auth.require_permission("collect_main_fees")
    def __init__(self, master=None):
        self.main_summary: dict = {}
        self.main_amount_var = None
        self.main_mode_var = None
        super().__init__(master)

    def load_dues(self) -> None:
        """Show one overall balance instead of pre-filling every fee head."""
        if self.selected_student_id is None:
            return
        for child in self.fee_frame.winfo_children():
            child.destroy()
        self.amount_vars.clear()
        self.mode_vars.clear()
        with connect_db() as conn:
            self.main_summary = main_collection_summary(conn, self.selected_student_id)
        total_due = float(self.main_summary["total_due"])
        due_date = self.main_summary["oldest_due_date"] or "Not set"
        self.fee_items = self.main_summary["charges"]

        tk.Label(
            self.fee_frame, text="Main Fee Collection", bg=SPLASH_BG, fg=SPLASH_FG,
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(4, 18))
        tk.Label(self.fee_frame, text="Total Due", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=0, sticky="w", pady=6)
        tk.Label(self.fee_frame, text=format_currency(total_due), bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 12, "bold")).grid(row=1, column=1, sticky="w", pady=6)
        tk.Label(self.fee_frame, text="Due On", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=2, sticky="e", padx=(24, 6), pady=6)
        tk.Label(self.fee_frame, text=due_date, bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=3, sticky="w", pady=6)
        tk.Label(self.fee_frame, text="Receipt Fee Head", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=2, column=0, sticky="w", pady=6)
        tk.Label(self.fee_frame, text="Tuition Fee", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=2, column=1, sticky="w", pady=6)

        self.main_amount_var = tk.StringVar(value="")
        self.main_mode_var = tk.StringVar(value="Cash")
        tk.Label(self.fee_frame, text="Amount to Collect", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=3, column=0, sticky="w", pady=8)
        amount_entry = ttk.Entry(self.fee_frame, textvariable=self.main_amount_var, width=18)
        amount_entry.grid(row=3, column=1, sticky="w", pady=8)
        amount_entry.bind("<KeyRelease>", lambda _event: self.update_summary())
        tk.Label(self.fee_frame, text="Payment Mode", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=3, column=2, sticky="e", padx=(24, 6), pady=8)
        mode_combo = ttk.Combobox(self.fee_frame, textvariable=self.main_mode_var, values=MODE_LABELS, state="readonly", width=12)
        mode_combo.grid(row=3, column=3, sticky="w", pady=8)
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._capture_main_mode())
        self.summary_var.set(f"Total due: {format_currency(total_due)} | Due on: {due_date}")
        amount_entry.focus_set()

    def _capture_main_mode(self) -> None:
        """Collect cheque or UPI details for the single main payment."""
        if not self.fee_items:
            return
        synthetic_id = int(self.fee_items[0]["charge_id"])
        self.amount_vars[synthetic_id] = self.main_amount_var
        self.mode_vars[synthetic_id] = self.main_mode_var
        self.capture_mode_detail(synthetic_id)

    def update_summary(self) -> None:
        """Show entered collection amount alongside overall due and date."""
        raw = self.main_amount_var.get().strip() if self.main_amount_var is not None else ""
        try:
            amount = Decimal(raw or "0")
        except InvalidOperation:
            amount = Decimal("0")
        self.summary_var.set(
            f"Collecting: {format_currency(amount)} | Total due: {format_currency(self.main_summary.get('total_due', 0))} "
            f"| Due on: {self.main_summary.get('oldest_due_date') or 'Not set'}"
        )

    def _payable_items(self) -> list[dict]:
        """Build one Tuition Fee payment allocated oldest-due-first across overall charges."""
        if not self.fee_items or self.main_amount_var is None or self.main_mode_var is None:
            return []
        raw = self.main_amount_var.get().strip()
        if not raw:
            return []
        with connect_db() as conn:
            maximum = max_payment_amount(conn)
        amount = validate_payment_amount(raw, self.main_summary["total_due"], maximum=maximum)
        first = self.fee_items[0]
        detail = dict(first)
        mode = MODE_TO_DB[self.main_mode_var.get()]
        allocations = []
        remaining = Decimal(str(amount))
        for charge in self.fee_items:
            if remaining <= 0:
                break
            allocated = min(remaining, Decimal(str(charge["balance"] or 0)))
            if allocated > 0:
                allocations.append({"charge_id": int(charge["charge_id"]), "amount": allocated})
                remaining -= allocated
        return [{
            "fee_head_id": self.main_summary["tuition_fee_head_id"],
            "charge_id": int(first["charge_id"]),
            "academic_year": first["academic_year"],
            "due_date": self.main_summary["oldest_due_date"],
            "name": "Tuition Fee",
            "amount_due": self.main_summary["total_due"],
            "amount_paying": amount,
            "balance_after": Decimal(str(self.main_summary["total_due"])) - Decimal(str(amount)),
            "mode": mode,
            "note": "MAIN COLLECTION",
            "upi_reference": detail.get("note", "") if mode == "UPI" else None,
            "cheque_no": detail.get("cheque_no", ""),
            "bank": detail.get("bank", ""),
            "allocations": allocations,
        }]

    def _validate_payable(self, payable: list[dict]) -> bool:
        if not payable:
            messagebox.showerror("Collection", "Enter the amount to collect.")
            return False
        item = payable[0]
        if item["mode"] == "CHEQUE" and (not item.get("cheque_no") or not item.get("bank")):
            messagebox.showerror("Cheque details", "Cheque number and bank are required.")
            return False
        if item["mode"] == "UPI" and not item.get("upi_reference"):
            messagebox.showerror("UPI details", "UPI transaction reference is required.")
            return False
        return True
