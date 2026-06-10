"""Dues viewing and export screen for SFMS."""

from __future__ import annotations

from datetime import datetime
import os
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, ensure_student_charges
from ledger_service import LedgerService
from ui_collection_common import connect_db
from utils import format_currency, today_str


class DuesWindow(tk.Toplevel):
    """Display student-wise or class-wide unpaid dues."""

    def __init__(self, master=None, overdue_threshold: int | None = None):
        """Create the dues window with an optional overdue-days filter."""
        super().__init__(master)
        self.overdue_threshold = overdue_threshold
        self.title("Dues")
        self.geometry("980x560")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.class_var = tk.StringVar()
        self.rows = []
        self.row_students: dict[str, dict] = {}
        self._build_widgets()
        self._load_classes()
        if self.overdue_threshold is not None:
            self.title(f"Dues — {self.overdue_threshold}+ Days Overdue")
            self.after(0, self.load_dues)

    def _build_widgets(self) -> None:
        """Build filters, dues table, and export action."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Search", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Entry(top, textvariable=self.search_var, width=28).pack(side="left", padx=6)
        tk.Label(top, text="Class", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(12, 0))
        self.class_combo = ttk.Combobox(top, textvariable=self.class_var, state="readonly", width=18)
        self.class_combo.pack(side="left", padx=6)
        ttk.Button(top, text="Load", command=self.load_dues).pack(side="left", padx=6)
        ttk.Button(top, text="Export", command=self.export).pack(side="right")
        ttk.Button(top, text="Print Dues Statement", command=self.print_student_statement).pack(side="right", padx=4)
        ttk.Button(top, text="Issue TC", command=self.issue_tc).pack(side="right", padx=4)
        columns = ("student", "fee_head", "amount_due", "paid", "balance", "due_date", "days")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        headings = ("Student", "Fee Head", "Amount Due", "Paid", "Balance", "Due Date", "Days Overdue")
        for column, heading in zip(columns, headings):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=130)
        self.tree.tag_configure("overdue", foreground="red")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)

    def _load_classes(self) -> None:
        """Populate class dropdown from database rows."""
        with connect_db() as conn:
            classes = [row[0] for row in conn.execute("SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> '' ORDER BY class")]
        self.class_combo.configure(values=[""] + classes)

    def load_dues(self) -> None:
        """Load dues for matching students and classes."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        class_filter = self.class_var.get()
        self.rows = []
        self.row_students: dict[str, dict] = {}
        with connect_db() as conn:
            year = active_academic_year(conn)
            ensure_student_charges(conn, year)
            rows = LedgerService(conn).get_all_outstanding(year)
            rows = [row for row in rows if (
                (not class_filter or row["student_class"] == class_filter)
                and (self.search_var.get().strip().lower() in str(row["student"]).lower()
                     or self.search_var.get().strip().lower() in str(row.get("aadhaar") or "").lower())
            )]
        if self.overdue_threshold is not None:
            rows = [row for row in rows if days_overdue(row["due_date"]) >= self.overdue_threshold]
        for row in rows:
            days = days_overdue(row["due_date"])
            values = (
                row["student"], row["fee_head"], format_currency(row["amount_due"] or 0),
                format_currency(row["paid"] or 0), format_currency(row["outstanding"] or 0), row["due_date"] or "", days,
            )
            row_data = dict(row) | {"days_overdue": days}
            self.rows.append(row_data)
            item = self.tree.insert("", "end", values=values, tags=("overdue",) if days > 30 else ())
            self.row_students[item] = row_data

    def _selected_student(self) -> dict | None:
        """Return student metadata for the selected dues row."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Dues", "Select a student dues row first.", parent=self)
            return None
        return self.row_students.get(selected[0])

    @auth.require_role("ADMIN")
    def issue_tc(self) -> None:
        """Issue or override a transfer certificate from the selected dues row."""
        auth.touch_session()
        row = self._selected_student()
        if row is None:
            return
        from ui_students import DuesClearanceDialog

        DuesClearanceDialog(self, int(row["student_id"]), on_issued=self._tc_issued)

    def _tc_issued(self, path: str) -> None:
        """Open the TC and refresh dues after successful archival."""
        self.load_dues()
        if hasattr(os, "startfile"):
            os.startfile(path)
        messagebox.showinfo("Transfer Certificate", f"TC saved to:\n{path}", parent=self)

    def print_student_statement(self) -> None:
        """Generate a single-student dues statement PDF."""
        auth.touch_session()
        row = self._selected_student()
        if row is None:
            return
        from report_generator import classwise_dues_report

        try:
            with connect_db() as conn:
                year = active_academic_year(conn)
                path = classwise_dues_report(conn, row["student_class"], year, int(row["student_id"]))
        except Exception as exc:
            messagebox.showerror("Dues Statement", str(exc), parent=self)
            return
        if hasattr(os, "startfile"):
            os.startfile(path)
        messagebox.showinfo("Dues Statement", f"PDF saved to:\n{path}", parent=self)

    def export(self) -> None:
        """Export dues using the report-generator stub."""
        auth.touch_session()
        from report_generator import classwise_dues_report

        class_name = self.class_var.get().strip()
        if not class_name:
            messagebox.showerror("Dues export", "Select a class before exporting the classwise report.")
            return
        with connect_db() as conn:
            academic_year = active_academic_year(conn)
            result = classwise_dues_report(conn, class_name, academic_year)
        messagebox.showinfo("Dues export", f"Dues report saved to: {result}")


def days_overdue(due_date: str | None) -> int:
    """Return days overdue from DD-MM-YYYY due date text."""
    if not due_date:
        return 0
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            due = datetime.strptime(due_date, fmt)
            today = datetime.strptime(today_str(), "%d-%m-%Y")
            return max(0, (today - due).days)
        except ValueError:
            continue
    return 0
