"""Advance-payment collection screen for SFMS."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from audit import log_action
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, allocate_payment, ensure_student_charges
from ui_collection_common import connect_db, require_session, search_students
from utils import compute_hash, generate_receipt_no, now_str, today_str


class AdvancePaymentWindow(tk.Toplevel):
    """Collect advance payment as an immutable credit payment row."""

    def __init__(self, master=None):
        """Create the advance-payment window."""
        super().__init__(master)
        self.title("Advance Payment")
        self.geometry("760x520")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.fee_head_var = tk.StringVar()
        self.term_var = tk.StringVar()
        self.selected_student_id: int | None = None
        self.fee_head_ids: dict[str, int] = {}
        self._build_widgets()

    def _build_widgets(self) -> None:
        """Build search results and advance-entry controls."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Search Student", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(top, textvariable=self.search_var, width=34).pack(side="left", padx=8)
        ttk.Button(top, text="Search", command=self.search).pack(side="left")
        self.tree = ttk.Treeview(self, columns=("id", "name", "class", "aadhaar"), show="headings", height=6)
        for column in ("id", "name", "class", "aadhaar"):
            self.tree.heading(column, text=column.title())
        self.tree.pack(fill="x", padx=12, pady=8)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.load_student_terms())

        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="x", padx=12, pady=16)
        tk.Label(form, text="Fee Head", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=0, column=0, sticky="w", pady=5)
        self.fee_combo = ttk.Combobox(form, textvariable=self.fee_head_var, state="readonly", width=32)
        self.fee_combo.grid(row=0, column=1, pady=5)
        tk.Label(form, text="Future Term", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=0, sticky="w", pady=5)
        self.term_combo = ttk.Combobox(form, textvariable=self.term_var, state="readonly", width=32)
        self.term_combo.grid(row=1, column=1, pady=5)
        tk.Label(form, text="Amount", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.amount_var).grid(row=2, column=1, pady=5)
        ttk.Button(form, text="Save Advance", command=self.save).grid(row=3, column=0, columnspan=2, pady=16)

    def search(self) -> None:
        """Search active students by name or Aadhaar."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in search_students(self.search_var.get().strip()):
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["name"], row["class"], row["aadhaar"] or ""))

    def load_student_terms(self) -> None:
        """Load future due dates and fee heads for selected student."""
        auth.touch_session()
        selection = self.tree.selection()
        if not selection:
            return
        self.selected_student_id = int(selection[0])
        with connect_db() as conn:
            student = conn.execute("SELECT class FROM students WHERE id = ?", (self.selected_student_id,)).fetchone()
            year = active_academic_year(conn)
            ensure_student_charges(conn, year, self.selected_student_id)
            rows = conn.execute(
                """
                SELECT l.charge_id, l.fee_head_id, fh.name, l.due_date, l.balance
                FROM charge_ledger l JOIN fee_heads fh ON fh.id=l.fee_head_id
                WHERE l.student_id=? AND l.academic_year=? AND l.due_date IS NOT NULL
                      AND l.due_date<>'' AND l.balance>0
                ORDER BY l.due_date, fh.name
                """,
                (self.selected_student_id, year),
            ).fetchall()
        self.charge_options = {f"{row['name']} ({row['due_date']})": dict(row) for row in rows}
        self.fee_head_ids = {label: row["fee_head_id"] for label, row in self.charge_options.items()}
        labels = list(self.charge_options)
        self.fee_combo.configure(values=labels)
        self.term_combo.configure(values=[row["due_date"] for row in rows])
        if labels:
            self.fee_head_var.set(labels[0])
            self.term_var.set(rows[0]["due_date"])

    def save(self) -> None:
        """Insert an immutable advance payment with negative balance credit."""
        auth.touch_session()
        if self.selected_student_id is None or not require_session():
            return
        try:
            amount = float(self.amount_var.get())
        except ValueError:
            messagebox.showerror("Validation", "Amount must be numeric.")
            return
        if amount <= 0:
            messagebox.showerror("Validation", "Amount must be greater than zero.")
            return
        option = self.charge_options.get(self.fee_head_var.get())
        if option is None:
            messagebox.showerror("Validation", "Select a fee head and term.")
            return
        fee_head_id = option["fee_head_id"]
        charge_id = option["charge_id"]
        if self.term_var.get() != str(option["due_date"] or ""):
            messagebox.showerror("Validation", "The selected term does not match the selected charge.")
            return
        if amount > float(option["balance"] or 0) + 0.005:
            messagebox.showerror("Validation", "Advance cannot exceed the selected future charge balance.")
            return
        with connect_db() as conn:
            receipt_no = generate_receipt_no(conn)
            payment_date = today_str()
            payment_hash = compute_hash(receipt_no, self.selected_student_id, amount, payment_date)
            cursor = conn.execute(
                """
                INSERT INTO payments (
                    student_id, receipt_no, fee_head_id, amount_due, amount_paid, balance,
                    payment_date, collected_by, payment_mode, note, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CASH', 'ADVANCE', ?)
                """,
                (self.selected_student_id, receipt_no, fee_head_id, 0, amount, 0, payment_date, auth.CURRENT_SESSION.user_id, payment_hash),
            )
            allocate_payment(conn, cursor.lastrowid, charge_id, amount, "ADVANCE")
            receipt_hash = compute_hash(receipt_no, self.selected_student_id, amount, payment_date)
            conn.execute(
                """
                INSERT INTO receipts (receipt_no, student_id, total_paid, receipt_type, printed_at, printed_by, reprint_count)
                VALUES (?, ?, ?, 'ADVANCE', ?, ?, 0)
                """,
                (receipt_no, self.selected_student_id, amount, now_str(), auth.CURRENT_SESSION.user_id),
            )
            conn.execute(
                "INSERT INTO receipt_hashes (receipt_no, sha256_hash, created_at) VALUES (?, ?, ?)",
                (receipt_no, receipt_hash, now_str()),
            )
            log_action(
                conn,
                auth.CURRENT_SESSION.user_id,
                "ADVANCE_PAYMENT",
                "payments",
                cursor.lastrowid,
                None,
                json.dumps({"receipt_no": receipt_no, "amount": amount}, default=str),
            )
        messagebox.showinfo("Advance payment", f"Advance payment saved. Receipt No: {receipt_no}")
