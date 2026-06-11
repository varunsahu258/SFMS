"""Dues viewing and export screen for SFMS."""

from __future__ import annotations

from datetime import datetime
import os
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import SPLASH_BG, SPLASH_FG
from ledger import active_academic_year, ensure_student_charges
from ledger_service import LedgerService
from ui_collection_common import connect_db
from utils import format_currency, today_str


def aggregate_student_dues(rows: list[dict]) -> list[dict]:
    """Collapse fee-head balances into one authoritative total per student."""
    students: dict[int, dict] = {}
    for row in rows:
        student_id = int(row["student_id"])
        due_date = str(row.get("due_date") or "")
        item = students.setdefault(student_id, {
            "student_id": student_id,
            "student": row.get("student") or "",
            "student_class": row.get("student_class") or "",
            "student_section": row.get("student_section") or "",
            "scholar_no": row.get("scholar_no") or "",
            "aadhaar": row.get("aadhaar") or "",
            "phone": row.get("phone") or "",
            "mobile2": row.get("mobile2") or "",
            "total_due": 0.0,
            "oldest_due_date": "",
        })
        item["total_due"] += float(row.get("outstanding") or 0)
        if due_date and (
            not item["oldest_due_date"]
            or _due_date_sort_key(due_date) < _due_date_sort_key(item["oldest_due_date"])
        ):
            item["oldest_due_date"] = due_date
    return sorted(
        students.values(),
        key=lambda item: (str(item["student_class"]), str(item["student"])),
    )


def _due_date_sort_key(value: str) -> datetime:
    """Return a sortable due date, putting invalid dates after valid dates."""
    for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), date_format)
        except ValueError:
            continue
    return datetime.max



class DuesWindow(WorkspacePage):
    """Display student-wise or class-wide unpaid dues."""

    @auth.require_permission("view_dues")
    def __init__(self, master=None, overdue_threshold: int | None = None, *, embedded: bool = False):
        """Create the dues window with an optional overdue-days filter."""
        super().__init__(master, embedded=embedded)
        self.overdue_threshold = overdue_threshold
        self.title("Dues")
        self.geometry("1050x580")
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
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=32)
        search_entry.pack(side="left", padx=6)
        search_entry.bind("<KeyRelease>", lambda _event: self.load_dues())
        search_entry.bind("<Return>", lambda _event: self.load_dues())
        tk.Label(top, text="Class", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(12, 0))
        self.class_combo = ttk.Combobox(top, textvariable=self.class_var, state="readonly", width=18)
        self.class_combo.pack(side="left", padx=6)
        ttk.Button(top, text="Load", command=self.load_dues).pack(side="left", padx=6)
        ttk.Button(top, text="Export", command=self.export).pack(side="right")
        ttk.Button(top, text="Print Dues Statement", command=self.print_student_statement).pack(side="right", padx=4)
        ttk.Button(top, text="Issue TC", command=self.issue_tc).pack(side="right", padx=4)
        columns = ("scholar_no", "student", "class", "total_due", "due_date", "days")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for column, heading, width in (
            ("scholar_no", "Scholar No.", 110), ("student", "Student", 260),
            ("class", "Class / Section", 150), ("total_due", "Total Due", 140),
            ("due_date", "Due On", 120), ("days", "Days Overdue", 110),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
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
        class_filter = self.class_var.get()
        self.rows = []
        self.row_students: dict[str, dict] = {}
        with connect_db() as conn:
            year = active_academic_year(conn)
            ensure_student_charges(conn, year)
            rows = aggregate_student_dues(LedgerService(conn).get_all_outstanding(year))
            search = self.search_var.get().strip().casefold()
            if search:
                existing_ids = {row["student_id"] for row in rows}
                students = conn.execute(
                    """SELECT id,name,class,section,scholar_no,aadhaar,phone,mobile2
                       FROM students WHERE is_active=1 ORDER BY class,name"""
                ).fetchall()
                for student in students:
                    candidate = {
                        "student_id": int(student["id"]), "student": student["name"] or "",
                        "student_class": student["class"] or "", "student_section": student["section"] or "",
                        "scholar_no": student["scholar_no"] or "", "aadhaar": student["aadhaar"] or "",
                        "phone": student["phone"] or "", "mobile2": student["mobile2"] or "",
                        "total_due": 0.0, "oldest_due_date": "",
                    }
                    if candidate["student_id"] not in existing_ids:
                        rows.append(candidate)
            rows = [row for row in rows if (
                (not class_filter or row["student_class"] == class_filter)
                and (not search or any(search in str(row.get(field) or "").casefold() for field in (
                    "student", "scholar_no", "aadhaar", "phone", "mobile2",
                )))
            )]
        if self.overdue_threshold is not None:
            rows = [row for row in rows if days_overdue(row["oldest_due_date"]) >= self.overdue_threshold]
        for row in rows:
            days = days_overdue(row["oldest_due_date"])
            class_text = f"{row['student_class']}{' / ' + row['student_section'] if row['student_section'] else ''}"
            values = (
                row["scholar_no"], row["student"], class_text,
                format_currency(row["total_due"]), row["oldest_due_date"], days,
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

    @auth.require_permission("manage_students")
    def issue_tc(self) -> None:
        """Issue or override a transfer certificate from the selected dues row."""
        auth.touch_session()
        row = self._selected_student()
        if row is None:
            return
        if not auth.can_override_financial_data():
            messagebox.showerror(
                "Transfer Certificate",
                "This student has unpaid dues. Only an administrator can override dues clearance.",
                parent=self,
            )
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
