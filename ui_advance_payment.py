"""Advance-payment collection screen for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, ensure_student_charges
from money import OverpaymentError, max_payment_amount, validate_payment_amount
from receipt_printing import PrintFailureDialog, print_committed_receipt
from ui_collection_common import connect_db, require_session, search_students
from financial_operations import record_advance_payment


class AdvancePaymentWindow(WorkspacePage):
    """Collect advance payment as an immutable credit payment row."""

    @auth.require_permission("collect_advance_payments")
    def __init__(self, master=None, *, embedded: bool = False):
        """Create the advance-payment window."""
        super().__init__(master, embedded=embedded)
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
                SELECT l.charge_id, l.fee_head_id, fh.name, l.due_date, l.balance,
                       ay.id AS academic_year_id, ay.label AS academic_year
                FROM charge_ledger l
                JOIN academic_years ay ON ay.label=l.academic_year JOIN fee_heads fh ON fh.id=l.fee_head_id
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
        """Insert an immutable, term-scoped advance payment and print its receipt."""
        auth.touch_session()
        if self.selected_student_id is None or not require_session():
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
        with connect_db() as conn:
            try:
                amount = validate_payment_amount(
                    self.amount_var.get(), option["balance"],
                    maximum=max_payment_amount(conn),
                )
            except OverpaymentError as exc:
                messagebox.showerror("Overpayment", str(exc), parent=self)
                return
            except ValueError as exc:
                messagebox.showerror("Validation", str(exc), parent=self)
                return
            try:
                result = record_advance_payment(
                    conn, self.selected_student_id, charge_id, fee_head_id, amount,
                    option["academic_year_id"], option["academic_year"],
                    str(option["due_date"]), auth.CURRENT_SESSION.user_id,
                )
            except Exception as exc:
                messagebox.showerror("Advance payment", str(exc), parent=self)
                return
            receipt_no = result["receipt_no"]
            committed_receipt_id = result["receipt_id"]
        try:
            print_committed_receipt(committed_receipt_id, receipt_no)
        except Exception as exc:
            PrintFailureDialog(self, committed_receipt_id, receipt_no, exc)
        messagebox.showinfo("Advance payment", f"Advance payment saved. Receipt No: {receipt_no}")
