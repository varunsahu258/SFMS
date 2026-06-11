"""Shared fee-collection UI and persistence helpers for SFMS."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, charge_rows
from money import OverpaymentError, max_payment_amount, validate_payment_amount
from payment_controls import normalize_reference
from receipt_printing import PrintFailureDialog, print_committed_receipt
from financial_operations import record_collection
from utils import format_currency

PAYMENT_MODES = ("CASH", "CHEQUE", "UPI")
MODE_LABELS = ("Cash", "Cheque", "UPI")
MODE_TO_DB = {"Cash": "CASH", "Cheque": "CHEQUE", "UPI": "UPI", "CASH": "CASH", "CHEQUE": "CHEQUE", "UPI": "UPI"}


def connect_db() -> sqlite3.Connection:
    """Open a SQLite connection with required pragmas for collection screens."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def require_session() -> bool:
    """Return True when a logged-in session exists for collection writes."""
    if auth.CURRENT_SESSION is None or not auth.current_user_can_write():
        messagebox.showerror("Login required", "Please log in before collecting fees.")
        return False
    auth.CURRENT_SESSION.touch()
    return True


def search_students(term: str) -> list[sqlite3.Row]:
    """Search active students by name or Aadhaar."""
    with connect_db() as conn:
        return conn.execute(
            """
            SELECT id, name, class, section, aadhaar, phone
            FROM students
            WHERE is_active = 1 AND (name LIKE ? OR aadhaar LIKE ?)
            ORDER BY name
            """,
            (f"%{term}%", f"%{term}%"),
        ).fetchall()


def student_by_id(conn: sqlite3.Connection, student_id: int) -> sqlite3.Row:
    """Return a student row by id."""
    return conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()


def fee_rows(conn: sqlite3.Connection, student_id: int, register_types: tuple[str, ...], force_exemptions: bool = False) -> list[dict]:
    """Build payable rows from academic-year-specific immutable charges."""
    rows = charge_rows(conn, student_id, active_academic_year(conn), register_types)
    result = []
    for row in rows:
        balance = float(row["balance"] or 0)
        adjustments = float(row["adjustments"] or 0)
        is_exempt = adjustments >= float(row["original_amount"] or 0) and balance <= 0
        result.append({
            "charge_id": int(row["charge_id"]),
            "fee_head_id": int(row["fee_head_id"]),
            "academic_year": row["academic_year"],
            "due_date": row["due_date"],
            "name": row["fee_head"],
            "amount_due": balance,
            "base_due": float(row["original_amount"] or 0),
            "paid": float(row["paid"] or 0),
            "adjustments": adjustments,
            "previous_balance": 0.0,
            "discount": adjustments,
            "amount_paying": max(0.0, balance),
            "mode": "Cash",
            "note": "EXEMPT" if is_exempt else "",
            "cheque_no": "",
            "bank": "",
            "is_exempt": is_exempt,
        })
    return result


class ChequeDetailDialog(tk.Toplevel):
    """Dialog for capturing cheque number and bank."""

    def __init__(self, master):
        """Create a modal cheque detail dialog."""
        super().__init__(master)
        self.title("Cheque Details")
        self.result = None
        self.cheque_var = tk.StringVar()
        self.bank_var = tk.StringVar()
        self._build()
        self.transient(master)
        self.grab_set()
        self.wait_window(self)

    def _build(self) -> None:
        """Build cheque detail fields."""
        frame = tk.Frame(self, padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Cheque No").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.cheque_var).grid(row=0, column=1, pady=4)
        tk.Label(frame, text="Bank").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.bank_var).grid(row=1, column=1, pady=4)
        ttk.Button(frame, text="OK", command=self._ok).grid(row=2, column=0, columnspan=2, pady=10)

    def _ok(self) -> None:
        """Validate and store cheque details."""
        auth.touch_session()
        if not self.cheque_var.get().strip() or not self.bank_var.get().strip():
            messagebox.showerror("Cheque details", "Cheque number and bank are required.")
            return
        self.result = {"cheque_no": normalize_reference(self.cheque_var.get()), "bank": self.bank_var.get().strip()}
        self.destroy()


class UPIDetailDialog(tk.Toplevel):
    """Dialog for capturing a UPI transaction reference."""

    def __init__(self, master):
        """Create a modal UPI detail dialog."""
        super().__init__(master)
        self.title("UPI Details")
        self.result = None
        self.ref_var = tk.StringVar()
        self._build()
        self.transient(master)
        self.grab_set()
        self.wait_window(self)

    def _build(self) -> None:
        """Build UPI detail fields."""
        frame = tk.Frame(self, padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Transaction Ref").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.ref_var).grid(row=0, column=1, pady=4)
        ttk.Button(frame, text="OK", command=self._ok).grid(row=1, column=0, columnspan=2, pady=10)

    def _ok(self) -> None:
        """Validate and store the UPI transaction reference."""
        auth.touch_session()
        if not self.ref_var.get().strip():
            messagebox.showerror("UPI details", "Transaction reference is required.")
            return
        self.result = {"transaction_ref": normalize_reference(self.ref_var.get())}
        self.destroy()


class CollectionBaseWindow(WorkspacePage):
    """Base window for BIG/SMALL/exemption fee collection flows."""

    register_types: tuple[str, ...] = ("BIG", "BOTH")
    receipt_type = "BIG"
    max_rows: int | None = None
    force_exemption_view = False

    def __init__(self, master=None, *, embedded: bool = False):
        """Create the shared fee collection screen."""
        super().__init__(master, embedded=embedded)
        self.title(f"{self.receipt_type} Fee Collection")
        self.geometry("1040x640")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.selected_student_id: int | None = None
        self.fee_items: list[dict] = []
        self.amount_vars: dict[int, tk.StringVar] = {}
        self.mode_vars: dict[int, tk.StringVar] = {}
        self.summary_var = tk.StringVar(value="Select a student to load dues.")
        self._build_widgets()

    def _build_widgets(self) -> None:
        """Build search results, fee-entry grid, and confirmation controls."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Search Student", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search_var, width=40)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Button(top, text="Search", command=self.search).pack(side="left")

        self.student_tree = ttk.Treeview(self, columns=("id", "name", "class"), show="headings", height=5)
        for column, heading, width in (("id", "ID", 60), ("name", "Name", 280), ("class", "Class", 120)):
            self.student_tree.heading(column, text=heading)
            self.student_tree.column(column, width=width)
        self.student_tree.pack(fill="x", padx=12)
        self.student_tree.bind("<<TreeviewSelect>>", lambda _event: self.load_selected_student())

        self.fee_frame = tk.Frame(self, bg=SPLASH_BG)
        self.fee_frame.pack(fill="both", expand=True, padx=12, pady=12)
        bottom = tk.Frame(self, bg=SPLASH_BG)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(bottom, textvariable=self.summary_var, bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Button(bottom, text="Confirm and Save", command=self.confirm_and_save).pack(side="right")

    def search(self) -> None:
        """Search students and display matching rows."""
        auth.touch_session()
        for item in self.student_tree.get_children():
            self.student_tree.delete(item)
        for row in search_students(self.search_var.get().strip()):
            self.student_tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["name"], row["class"]))

    def load_selected_student(self) -> None:
        """Load dues when a student is selected."""
        auth.touch_session()
        selection = self.student_tree.selection()
        if not selection:
            return
        self.selected_student_id = int(selection[0])
        self.load_dues()

    def load_dues(self) -> None:
        """Load fee-head dues for the selected student."""
        if self.selected_student_id is None:
            return
        for child in self.fee_frame.winfo_children():
            child.destroy()
        self.amount_vars.clear()
        self.mode_vars.clear()
        with connect_db() as conn:
            self.fee_items = fee_rows(conn, self.selected_student_id, self.register_types, self.force_exemption_view)
        hidden_count = 0
        if self.max_rows is not None and len(self.fee_items) > self.max_rows:
            hidden_count = len(self.fee_items) - self.max_rows
            self.fee_items = self.fee_items[: self.max_rows]
        headers = ("Fee Head", "Outstanding", "Paid", "Discount / Exemption", "Amount Paying", "Mode", "Status")
        for column, header in enumerate(headers):
            tk.Label(self.fee_frame, text=header, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 10, "bold")).grid(row=0, column=column, sticky="ew", padx=4, pady=4)
        for row_index, item in enumerate(self.fee_items, start=1):
            amount_var = tk.StringVar(value=f"{item['amount_paying']:.2f}")
            mode_var = tk.StringVar(value=item["mode"])
            self.amount_vars[item["charge_id"]] = amount_var
            self.mode_vars[item["charge_id"]] = mode_var
            state = "disabled" if item["is_exempt"] else "normal"
            tk.Label(self.fee_frame, text=item["name"], bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row_index, column=0, sticky="w", padx=4, pady=4)
            tk.Label(self.fee_frame, text=format_currency(item["amount_due"]), bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row_index, column=1, padx=4, pady=4)
            tk.Label(self.fee_frame, text=format_currency(item["paid"]), bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row_index, column=2, padx=4, pady=4)
            tk.Label(self.fee_frame, text=format_currency(item["discount"]), bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row_index, column=3, padx=4, pady=4)
            ttk.Entry(self.fee_frame, textvariable=amount_var, state=state, width=12).grid(row=row_index, column=4, padx=4, pady=4)
            combo = ttk.Combobox(self.fee_frame, textvariable=mode_var, values=MODE_LABELS, state="readonly", width=10)
            combo.grid(row=row_index, column=5, padx=4, pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _event, charge_id=item["charge_id"]: self.capture_mode_detail(charge_id))
            status = "EXEMPT" if item["is_exempt"] else ""
            tk.Label(self.fee_frame, text=status, bg=SPLASH_BG, fg="#999999" if status else SPLASH_FG).grid(row=row_index, column=6, padx=4, pady=4)
        if hidden_count:
            tk.Label(
                self.fee_frame,
                text=(f"{hidden_count} additional fee head(s) are not shown in Small Collection. "
                      "Use Main Collection to view and collect every outstanding head."),
                bg="#fff3cd", fg="#6b4f00", font=("Segoe UI", 10, "bold"),
                anchor="w", justify="left", padx=10, pady=8,
            ).grid(row=len(self.fee_items) + 1, column=0, columnspan=7, sticky="ew", padx=4, pady=(10, 4))
        self.update_summary()

    def capture_mode_detail(self, charge_id: int) -> None:
        """Open mode-specific detail dialogs for cheque or UPI entries."""
        auth.touch_session()
        item = self._item_by_charge(charge_id)
        mode = self.mode_vars[charge_id].get()
        if mode == "Cheque":
            dialog = ChequeDetailDialog(self)
            if dialog.result:
                item.update(dialog.result)
        elif mode == "UPI":
            dialog = UPIDetailDialog(self)
            if dialog.result:
                item["note"] = dialog.result["transaction_ref"]

    def _item_by_charge(self, charge_id: int) -> dict:
        """Return the loaded fee item for a student-charge id."""
        return next(item for item in self.fee_items if item["charge_id"] == charge_id)

    def update_summary(self) -> None:
        """Update the total summary label from amount entry values."""
        total = Decimal("0")
        for amount_var in self.amount_vars.values():
            try:
                value = Decimal((amount_var.get() or "0").strip())
                if value.is_finite():
                    total += value
            except (InvalidOperation, ValueError):
                continue
        self.summary_var.set(f"Total paying: {format_currency(total)}")

    def _payable_items(self) -> list[dict]:
        """Return fee items with parsed positive payment amounts."""
        payable = []
        with connect_db() as conn:
            maximum = max_payment_amount(conn)
        for item in self.fee_items:
            if item["is_exempt"]:
                continue
            raw_amount = (self.amount_vars[item["charge_id"]].get() or "0").strip()
            try:
                candidate = Decimal(raw_amount)
            except InvalidOperation as exc:
                raise ValueError("Amount paying must be a valid number.") from exc
            if candidate == 0:
                continue
            amount = validate_payment_amount(
                raw_amount, item["amount_due"], maximum=maximum
            )
            item = dict(item)
            item["amount_paying"] = amount
            item["mode"] = MODE_TO_DB[self.mode_vars[item["charge_id"]].get()]
            payable.append(item)
        return payable

    def _validate_payable(self, payable: list[dict]) -> bool:
        """Validate selected payment rows before saving."""
        if not payable:
            messagebox.showerror("Collection", "Enter an amount for at least one fee head.")
            return False
        for item in payable:
            if item["mode"] == "CHEQUE" and (not item.get("cheque_no") or not item.get("bank")):
                messagebox.showerror("Cheque details", f"Cheque details are required for {item['name']}.")
                return False
            if item["mode"] == "UPI" and not item.get("note"):
                messagebox.showerror("UPI details", f"UPI transaction reference is required for {item['name']}.")
                return False
            if item["amount_paying"] > float(item["amount_due"]) + 0.005:
                messagebox.showerror("Collection", f"Payment for {item['name']} cannot exceed its charge balance. Use Advance Payment for future fees.")
                return False
        return True

    def confirm_and_save(self) -> None:
        """Confirm total, insert immutable payment rows, and print the receipt."""
        auth.touch_session()
        if self.selected_student_id is None or not require_session():
            return
        try:
            payable = self._payable_items()
        except OverpaymentError as exc:
            messagebox.showerror("Overpayment", f"{exc} Use Advance Payment for future fees.")
            return
        except ValueError as exc:
            messagebox.showerror("Validation", str(exc))
            return
        if not self._validate_payable(payable):
            return
        total = sum(item["amount_paying"] for item in payable)
        if not messagebox.askyesno("Confirm collection", f"Collect {format_currency(total)}?"):
            return
        try:
            with connect_db() as conn:
                result = record_collection(
                    conn, self.selected_student_id, self.receipt_type,
                    auth.CURRENT_SESSION.user_id, payable,
                )
        except Exception as exc:
            messagebox.showerror("Collection", str(exc), parent=self)
            return
        receipt_no = result["receipt_no"]
        committed_receipt_id = result["receipt_id"]
        try:
            print_committed_receipt(committed_receipt_id, receipt_no)
        except Exception as exc:
            PrintFailureDialog(self, committed_receipt_id, receipt_no, exc)
        messagebox.showinfo("Payment collected", f"Payment collected successfully. Receipt No: {receipt_no}")
        self.load_dues()
