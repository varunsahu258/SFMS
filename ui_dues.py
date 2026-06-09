"""Dues viewing and export screen for SFMS."""

from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ui_collection_common import active_academic_year, connect_db
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
        with connect_db() as conn:
            year = active_academic_year(conn)
            params = [year, term, term]
            class_sql = ""
            if class_filter:
                class_sql = " AND s.class = ?"
                params.append(class_filter)
            rows = conn.execute(
                f"""
                SELECT s.name AS student, fh.name AS fee_head, fs.amount AS amount_due, fs.due_date,
                       COALESCE(SUM(p.amount_paid), 0) AS paid,
                       fs.amount - COALESCE(SUM(p.amount_paid), 0) AS balance,
                       MIN(p.payment_date) AS oldest_payment_date
                FROM students s
                JOIN fee_structure fs ON fs.class = s.class AND fs.academic_year = ?
                JOIN fee_heads fh ON fh.id = fs.fee_head_id
                LEFT JOIN payments p ON p.student_id = s.id AND p.fee_head_id = fs.fee_head_id
                WHERE s.is_active = 1 AND (s.name LIKE ? OR s.aadhaar LIKE ?){class_sql}
                GROUP BY s.id, fs.id
                HAVING balance <> 0
                ORDER BY s.class, s.name, fh.name
                """,
                params,
            ).fetchall()
        if self.overdue_threshold is not None:
            rows = [row for row in rows if days_overdue(row["oldest_payment_date"] or row["due_date"]) >= self.overdue_threshold]
        for row in rows:
            days = days_overdue(row["oldest_payment_date"] or row["due_date"])
            values = (
                row["student"], row["fee_head"], format_currency(row["amount_due"] or 0),
                format_currency(row["paid"] or 0), format_currency(row["balance"] or 0), row["due_date"] or "", days,
            )
            self.rows.append(dict(row) | {"days_overdue": days})
            self.tree.insert("", "end", values=values, tags=("overdue",) if days > 30 else ())

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
