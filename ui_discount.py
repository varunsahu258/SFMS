"""Discount recording screen for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, add_adjustment, ensure_student_charges
from ui_collection_common import connect_db, search_students
from utils import now_str


class DiscountWindow(tk.Toplevel):
    """Admin-only window for creating student fee discounts."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the discount window."""
        super().__init__(master)
        self.title("Discount")
        self.geometry("760x520")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.fee_head_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.selected_student_id: int | None = None
        self.fee_head_ids = {}
        self._build_widgets()
        self._load_fee_heads()

    def _build_widgets(self) -> None:
        """Build student search and discount form."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Entry(top, textvariable=self.search_var, width=34).pack(side="left", padx=6)
        ttk.Button(top, text="Search", command=self.search).pack(side="left")
        self.tree = ttk.Treeview(self, columns=("id", "name", "class", "aadhaar"), show="headings", height=6)
        for column in ("id", "name", "class", "aadhaar"):
            self.tree.heading(column, text=column.title())
        self.tree.pack(fill="x", padx=12, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self._select_student)
        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="x", padx=12, pady=14)
        for row, (label, var) in enumerate((("Fee Head", self.fee_head_var), ("Amount", self.amount_var), ("Reason", self.reason_var))):
            tk.Label(form, text=label, bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row, column=0, sticky="w", pady=5)
            if label == "Fee Head":
                ttk.Combobox(form, textvariable=var, state="readonly", width=30).grid(row=row, column=1, pady=5, sticky="ew")
                self.fee_combo = form.grid_slaves(row=row, column=1)[0]
            else:
                ttk.Entry(form, textvariable=var, width=32).grid(row=row, column=1, pady=5, sticky="ew")
        ttk.Button(form, text="Save Discount", command=self.save).grid(row=3, column=0, columnspan=2, pady=16)

    def _load_fee_heads(self) -> None:
        """Load active fee heads into the dropdown."""
        with connect_db() as conn:
            rows = conn.execute("SELECT id, name FROM fee_heads WHERE is_active = 1 ORDER BY name").fetchall()
        self.fee_head_ids = {row["name"]: row["id"] for row in rows}
        self.fee_combo.configure(values=list(self.fee_head_ids))

    def search(self) -> None:
        """Search students for discount assignment."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in search_students(self.search_var.get().strip()):
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["name"], row["class"], row["aadhaar"] or ""))

    def _select_student(self, _event) -> None:
        """Store the selected student id."""
        auth.touch_session()
        selection = self.tree.selection()
        self.selected_student_id = int(selection[0]) if selection else None

    @auth.require_role("ADMIN")
    def save(self) -> None:
        """Insert a discount row; trigger handles audit logging."""
        if self.selected_student_id is None:
            messagebox.showerror("Validation", "Select a student.")
            return
        try:
            amount = float(self.amount_var.get())
        except ValueError:
            messagebox.showerror("Validation", "Amount must be numeric.")
            return
        fee_head_id = self.fee_head_ids.get(self.fee_head_var.get())
        if fee_head_id is None or amount <= 0 or not self.reason_var.get().strip():
            messagebox.showerror("Validation", "Fee head, positive amount, and reason are required.")
            return
        with connect_db() as conn:
            year = active_academic_year(conn)
            ensure_student_charges(conn, year, self.selected_student_id)
            charges = conn.execute(
                "SELECT charge_id,balance,due_date FROM charge_ledger WHERE student_id=? AND academic_year=? AND fee_head_id=? AND status<>'CANCELLED' AND balance>0 ORDER BY due_date,charge_id",
                (self.selected_student_id, year, fee_head_id),
            ).fetchall()
            if not charges:
                messagebox.showerror("Discount", "No outstanding charge exists for this fee head in the active academic year.", parent=self)
                return
            if len(charges) != 1:
                messagebox.showerror(
                    "Discount",
                    "This fee head has multiple term charges. Apply the discount from a charge-specific workflow; no charge was changed.",
                    parent=self,
                )
                return
            charge = charges[0]
            if amount > float(charge["balance"] or 0) + 0.005:
                messagebox.showerror("Discount", "Discount cannot exceed the active-year outstanding charge.", parent=self)
                return
            cursor = conn.execute(
                "INSERT INTO discounts (student_id, fee_head_id, amount, reason, approved_by, created_at, academic_year, charge_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.selected_student_id, fee_head_id, amount, self.reason_var.get().strip(), auth.CURRENT_SESSION.user_id, now_str(), year, charge["charge_id"]),
            )
            add_adjustment(conn, charge["charge_id"], "DISCOUNT", amount, "discounts", cursor.lastrowid, self.reason_var.get().strip(), auth.CURRENT_SESSION.user_id)
        messagebox.showinfo("Discount", "Discount saved.")
