"""Main BIG-register collection with explicit fee-head selection."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ledger import active_academic_year, charge_rows
from money import max_payment_amount, validate_payment_amount
from ui_collection_common import MODE_LABELS, MODE_TO_DB, CollectionBaseWindow, connect_db
from utils import format_currency

CARD_BG = "#ffffff"
PAGE_BG = "#f6f4fb"
TEXT = "#241f2d"
MUTED = "#756d80"
ACCENT = "#5b3fc0"
BORDER = "#e7e1f0"


def _due_key(row: dict) -> tuple[datetime, int]:
    value = str(row.get("due_date") or "")
    for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format), int(row["charge_id"])
        except ValueError:
            continue
    return datetime.max, int(row["charge_id"])


def main_collection_summary(conn, student_id: int) -> dict:
    """Return every outstanding main-register fee head for explicit selection."""
    rows = [
        dict(row)
        for row in charge_rows(conn, student_id, active_academic_year(conn), ("BIG", "BOTH"))
        if float(row["balance"] or 0) > 0
    ]
    rows.sort(key=_due_key)
    for row in rows:
        row["name"] = str(row.get("fee_head") or "Fee")
        row["amount_due"] = float(row.get("balance") or 0)
        row["amount_paying"] = 0.0
        row["mode"] = "Cash"
        row["is_exempt"] = False
    return {
        "total_due": sum(float(row["balance"] or 0) for row in rows),
        "oldest_due_date": str(rows[0].get("due_date") or "") if rows else "",
        "charges": rows,
    }


def selected_main_collection_items(
    charges: list[dict], selected: dict[int, bool], amounts: dict[int, str],
    payment_mode: str, maximum,
) -> list[dict]:
    """Build one correctly matched payment/allocation row per selected fee head."""
    payable = []
    mode = MODE_TO_DB[payment_mode]
    for charge in charges:
        charge_id = int(charge["charge_id"])
        if not selected.get(charge_id, False):
            continue
        raw = str(amounts.get(charge_id, "")).strip()
        if not raw:
            raise ValueError(f"Enter an amount for {charge['name']}.")
        amount = validate_payment_amount(raw, charge["balance"], maximum=maximum)
        payable.append({
            **dict(charge),
            "fee_head_id": int(charge["fee_head_id"]),
            "charge_id": charge_id,
            "name": charge["name"],
            "amount_due": float(charge["balance"] or 0),
            "amount_paying": amount,
            "balance_after": Decimal(str(charge["balance"] or 0)) - Decimal(str(amount)),
            "mode": mode,
            "note": "MAIN COLLECTION",
            "allocations": [{"charge_id": charge_id, "amount": amount}],
        })
    if not payable:
        raise ValueError("Select at least one fee head to collect.")
    return payable


class CollectionMainWindow(CollectionBaseWindow):
    """Collect fees only against heads explicitly selected by the operator."""

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"

    @auth.require_permission("collect_main_fees")
    def __init__(self, master=None, *, embedded: bool = False):
        self.main_summary: dict = {}
        self.selected_head_vars: dict[int, tk.BooleanVar] = {}
        self.amount_entries: dict[int, ttk.Entry] = {}
        self.main_mode_var: tk.StringVar | None = None
        super().__init__(master, embedded=embedded)
        self.configure(bg=PAGE_BG)

    def _build_widgets(self) -> None:
        """Build a friendly search-first Main Collection workspace."""
        self.configure(bg=PAGE_BG)
        page = tk.Frame(self, bg=PAGE_BG)
        page.pack(fill="both", expand=True, padx=22, pady=18)

        tk.Label(page, text="Collect student fees", bg=PAGE_BG, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(
            page,
            text="Find a student, choose the fee heads being paid, then enter an amount for each selection.",
            bg=PAGE_BG, fg=MUTED, font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(3, 14))

        search_card = tk.Frame(page, bg=CARD_BG, highlightthickness=1,
                               highlightbackground=BORDER, padx=16, pady=14)
        search_card.pack(fill="x")
        tk.Label(search_card, text="Student search", bg=CARD_BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        entry = ttk.Entry(search_card, textvariable=self.search_var, width=46)
        entry.pack(side="left", padx=12, ipady=4)
        entry.bind("<KeyRelease>", lambda _event: self.search())
        tk.Button(
            search_card, text="Search", command=self.search, bg=ACCENT, fg="white",
            activebackground="#49309f", activeforeground="white", relief="flat",
            bd=0, padx=20, pady=8, font=("Segoe UI", 9, "bold"), cursor="hand2",
        ).pack(side="left")

        style = ttk.Style(self)
        style.configure("MainCollection.Treeview", rowheight=30, font=("Segoe UI", 10),
                        background=CARD_BG, fieldbackground=CARD_BG, foreground=TEXT)
        style.configure("MainCollection.Treeview.Heading", font=("Segoe UI", 9, "bold"),
                        background="#eee9f7", foreground=TEXT)
        self.student_tree = ttk.Treeview(
            page, columns=("id", "name", "class"), show="headings", height=5,
            style="MainCollection.Treeview",
        )
        for column, heading, width in (("id", "ID", 80), ("name", "Student name", 360), ("class", "Class", 160)):
            self.student_tree.heading(column, text=heading)
            self.student_tree.column(column, width=width, anchor="w")
        self.student_tree.pack(fill="x", pady=(10, 0))
        self.student_tree.bind("<<TreeviewSelect>>", lambda _event: self.load_selected_student())

        self.fee_frame = tk.Frame(page, bg=PAGE_BG)
        self.fee_frame.pack(fill="both", expand=True, pady=(14, 8))
        bottom = tk.Frame(page, bg=PAGE_BG)
        bottom.pack(fill="x", pady=(4, 0))
        tk.Label(bottom, textvariable=self.summary_var, bg=PAGE_BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(
            bottom, text="Review and save payment", command=self.confirm_and_save,
            bg=ACCENT, fg="white", activebackground="#49309f", activeforeground="white",
            relief="flat", bd=0, padx=22, pady=10, font=("Segoe UI", 10, "bold"), cursor="hand2",
        ).pack(side="right")
        entry.focus_set()

    def load_dues(self) -> None:
        """Show all outstanding heads with unselected checkboxes and gated amounts."""
        if self.selected_student_id is None:
            return
        for child in self.fee_frame.winfo_children():
            child.destroy()
        self.amount_vars.clear()
        self.mode_vars.clear()
        self.selected_head_vars.clear()
        self.amount_entries.clear()
        with connect_db() as conn:
            self.main_summary = main_collection_summary(conn, self.selected_student_id)
        self.fee_items = self.main_summary["charges"]
        total_due = float(self.main_summary["total_due"])
        due_date = self.main_summary["oldest_due_date"] or "Not set"

        self.fee_frame.configure(bg=PAGE_BG)
        heading = tk.Frame(self.fee_frame, bg=PAGE_BG)
        heading.pack(fill="x", pady=(2, 12))
        tk.Label(heading, text="Choose what the payment is for", bg=PAGE_BG, fg=TEXT,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(
            heading,
            text="Nothing is selected automatically. Tick a fee head first, then enter the amount for that head.",
            bg=PAGE_BG, fg=MUTED, font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(3, 0))

        summary_card = tk.Frame(self.fee_frame, bg=CARD_BG, highlightthickness=1,
                                highlightbackground=BORDER, padx=18, pady=12)
        summary_card.pack(fill="x", pady=(0, 12))
        tk.Label(summary_card, text="TOTAL OUTSTANDING", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(summary_card, text=format_currency(total_due), bg=CARD_BG, fg=ACCENT,
                 font=("Segoe UI", 15, "bold")).pack(side="left", padx=(10, 28))
        tk.Label(summary_card, text=f"Oldest due date: {due_date}", bg=CARD_BG, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side="left")

        list_card = tk.Frame(self.fee_frame, bg=CARD_BG, highlightthickness=1,
                             highlightbackground=BORDER, padx=12, pady=10)
        list_card.pack(fill="both", expand=True)
        headers = ("Select", "Fee head", "Outstanding", "Due date", "Amount to collect")
        widths = (9, 30, 18, 18, 22)
        for column, (text, width) in enumerate(zip(headers, widths)):
            tk.Label(list_card, text=text, width=width, anchor="w", bg=CARD_BG, fg=MUTED,
                     font=("Segoe UI", 9, "bold")).grid(row=0, column=column, sticky="ew", padx=5, pady=(2, 8))

        if not self.fee_items:
            tk.Label(list_card, text="No outstanding main-register fees for this student.",
                     bg=CARD_BG, fg=MUTED, font=("Segoe UI", 11)).grid(
                         row=1, column=0, columnspan=5, sticky="w", padx=8, pady=20)
        for row_index, item in enumerate(self.fee_items, 1):
            charge_id = int(item["charge_id"])
            selected_var = tk.BooleanVar(value=False)
            amount_var = tk.StringVar(value="")
            self.selected_head_vars[charge_id] = selected_var
            self.amount_vars[charge_id] = amount_var
            self.mode_vars[charge_id] = tk.StringVar(value="Cash")
            check = ttk.Checkbutton(
                list_card, variable=selected_var,
                command=lambda value=charge_id: self._toggle_head(value),
            )
            check.grid(row=row_index, column=0, sticky="w", padx=8, pady=7)
            tk.Label(list_card, text=item["name"], bg=CARD_BG, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).grid(row=row_index, column=1, sticky="w", padx=5)
            tk.Label(list_card, text=format_currency(item["balance"]), bg=CARD_BG, fg=TEXT,
                     font=("Segoe UI", 10)).grid(row=row_index, column=2, sticky="w", padx=5)
            tk.Label(list_card, text=item.get("due_date") or "Not set", bg=CARD_BG, fg=MUTED,
                     font=("Segoe UI", 10)).grid(row=row_index, column=3, sticky="w", padx=5)
            entry = ttk.Entry(list_card, textvariable=amount_var, state="disabled", width=18)
            entry.grid(row=row_index, column=4, sticky="w", padx=5)
            entry.bind("<KeyRelease>", lambda _event: self.update_summary())
            self.amount_entries[charge_id] = entry

        controls = tk.Frame(self.fee_frame, bg=PAGE_BG)
        controls.pack(fill="x", pady=(12, 0))
        tk.Label(controls, text="Payment method", bg=PAGE_BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.main_mode_var = tk.StringVar(value="Cash")
        mode_combo = ttk.Combobox(controls, textvariable=self.main_mode_var,
                                  values=MODE_LABELS, state="readonly", width=15)
        mode_combo.pack(side="left", padx=10)
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._capture_main_mode())
        tk.Label(controls, text="The chosen method applies to all selected heads.",
                 bg=PAGE_BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=8)
        self.update_summary()

    def _toggle_head(self, charge_id: int) -> None:
        """Enable the amount only after a fee head is explicitly checked."""
        selected = self.selected_head_vars[charge_id].get()
        entry = self.amount_entries[charge_id]
        entry.configure(state="normal" if selected else "disabled")
        if selected:
            entry.focus_set()
        else:
            self.amount_vars[charge_id].set("")
        self.update_summary()

    def _capture_main_mode(self) -> None:
        """Capture one cheque/UPI reference and apply it to selected payment rows."""
        selected_ids = [charge_id for charge_id, variable in self.selected_head_vars.items() if variable.get()]
        if not selected_ids or self.main_mode_var is None:
            if self.main_mode_var is not None and self.main_mode_var.get() != "Cash":
                messagebox.showinfo("Select fee heads", "Select at least one fee head before choosing cheque or UPI.", parent=self)
                self.main_mode_var.set("Cash")
            return
        first_id = selected_ids[0]
        for charge_id in selected_ids:
            self.mode_vars[charge_id].set(self.main_mode_var.get())
        self.capture_mode_detail(first_id)
        detail = self._item_by_charge(first_id)
        for charge_id in selected_ids[1:]:
            target = self._item_by_charge(charge_id)
            for key in ("note", "cheque_no", "bank"):
                if key in detail:
                    target[key] = detail[key]

    def update_summary(self) -> None:
        """Show selected-head count and the valid entered total."""
        total = Decimal("0")
        selected_count = 0
        for charge_id, selected in self.selected_head_vars.items():
            if not selected.get():
                continue
            selected_count += 1
            try:
                value = Decimal((self.amount_vars[charge_id].get() or "0").strip())
                if value.is_finite():
                    total += value
            except InvalidOperation:
                continue
        self.summary_var.set(
            f"Selected heads: {selected_count}   •   Collecting: {format_currency(total)}   •   "
            f"Total outstanding: {format_currency(self.main_summary.get('total_due', 0))}"
        )

    def _payable_items(self) -> list[dict]:
        """Return one payment row for every explicitly selected fee head."""
        if self.main_mode_var is None:
            return []
        with connect_db() as conn:
            maximum = max_payment_amount(conn)
        payable = selected_main_collection_items(
            self.fee_items,
            {charge_id: variable.get() for charge_id, variable in self.selected_head_vars.items()},
            {charge_id: variable.get() for charge_id, variable in self.amount_vars.items()},
            self.main_mode_var.get(),
            maximum,
        )
        detail_source = next((item for item in self.fee_items if item.get("note") or item.get("cheque_no")), {})
        for item in payable:
            if item["mode"] == "UPI":
                item["upi_reference"] = detail_source.get("note", "")
            elif item["mode"] == "CHEQUE":
                item["cheque_no"] = detail_source.get("cheque_no", "")
                item["bank"] = detail_source.get("bank", "")
        return payable

    def _validate_payable(self, payable: list[dict]) -> bool:
        """Validate details for every selected head before the transaction begins."""
        if not payable:
            messagebox.showerror("Collection", "Select at least one fee head and enter its amount.", parent=self)
            return False
        for item in payable:
            if item["mode"] == "CHEQUE" and (not item.get("cheque_no") or not item.get("bank")):
                messagebox.showerror("Cheque details", "Cheque number and bank are required.", parent=self)
                return False
            if item["mode"] == "UPI" and not item.get("upi_reference"):
                messagebox.showerror("UPI details", "UPI transaction reference is required.", parent=self)
                return False
        return True
