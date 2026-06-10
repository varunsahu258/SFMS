"""Tabbed PDF report center for SFMS."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import auth
from config import DB_PATH, REPORTS_DIR, SPLASH_BG, SPLASH_FG
from report_generator import (
    audit_export,
    cashflow_chart_report,
    classwise_dues_report,
    daily_report,
    comparative_report,
    defaulter_report,
    discount_register_report,
    feehead_collection_report,
    monthly_report,
    void_report,
    ytd_report,
)
from utils import today_str

MONTHS = tuple(datetime(2000, month, 1).strftime("%B") for month in range(1, 13))


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite report connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _open_pdf(path: str) -> None:
    """Open a generated PDF with the Windows shell when supported."""
    if hasattr(os, "startfile"):
        os.startfile(path)


class ReportsWindow(tk.Toplevel):
    """Display all SFMS PDF reports in a tabbed window."""

    @auth.require_permission("view_reports")
    def __init__(self, master=None):
        """Create report tabs and load dropdown values."""
        super().__init__(master)
        self.title("SFMS Reports")
        self.geometry("1180x580")
        self.configure(bg=SPLASH_BG)
        self.years: list[str] = []
        self.classes: list[str] = []
        self.users: dict[str, int] = {}
        self._load_options()
        self._build_widgets()

    def _load_options(self) -> None:
        """Load academic years, classes, and audit users."""
        with _connect() as conn:
            self.years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label DESC")]
            self.classes = [row[0] for row in conn.execute("SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> '' ORDER BY class")]
            self.users = {row["username"]: row["id"] for row in conn.execute("SELECT id, username FROM users ORDER BY username")}

    def _build_widgets(self) -> None:
        """Build report notebook and role-appropriate tabs."""
        tk.Label(self, text="Reports", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 20, "bold")).pack(pady=(14, 8))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._build_daily_tab(self._tab(notebook, "Daily"))
        self._build_monthly_tab(self._tab(notebook, "Monthly"))
        self._build_class_dues_tab(self._tab(notebook, "Class Dues"))
        self._build_defaulter_tab(self._tab(notebook, "Defaulter"))
        self._build_year_tab(self._tab(notebook, "YTD"), "Generate YTD PDF", ytd_report)
        self._build_year_tab(self._tab(notebook, "Cashflow"), "Generate Cashflow PDF", cashflow_chart_report)
        self._build_feehead_tab(self._tab(notebook, "Fee Head Collection"))
        self._build_comparative_tab(self._tab(notebook, "Comparative"))
        self._build_discount_tab(self._tab(notebook, "Discounts"))
        self._build_void_tab(self._tab(notebook, "Voids"))
        if auth.has_permission("view_audit_log"):
            self._build_audit_tab(self._tab(notebook, "Audit"))

    def _tab(self, notebook: ttk.Notebook, title: str) -> tk.Frame:
        """Create and register a padded notebook tab."""
        frame = tk.Frame(notebook, bg=SPLASH_BG, padx=24, pady=24)
        notebook.add(frame, text=title)
        return frame

    def _row(self, frame: tk.Frame, label: str, row: int) -> tk.Frame:
        """Create a standard label/control row."""
        holder = tk.Frame(frame, bg=SPLASH_BG)
        holder.grid(row=row, column=0, sticky="ew", pady=7)
        tk.Label(holder, text=label, width=20, anchor="w", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        return holder

    def _year_combo(self, holder: tk.Frame, variable: tk.StringVar) -> ttk.Combobox:
        """Add an academic-year dropdown with the latest year selected."""
        combo = ttk.Combobox(holder, textvariable=variable, values=self.years, state="readonly", width=25)
        combo.pack(side="left")
        if self.years:
            variable.set(self.years[0])
        return combo

    def _generate(self, generator, *args) -> None:
        """Run a report generator, notify the user, and open the PDF on Windows."""
        auth.touch_session()
        try:
            with _connect() as conn:
                path = generator(conn, *args)
        except Exception as exc:
            messagebox.showerror("Report generation", str(exc), parent=self)
            return
        _open_pdf(path)
        messagebox.showinfo("Report generated", f"PDF saved to:\n{path}", parent=self)

    def _generate_pair(self, generator, args: tuple, open_folder: bool = False) -> None:
        """Generate a PDF and companion workbook, then open the selected output."""
        auth.touch_session()
        try:
            with _connect() as conn:
                pdf_path = generator(conn, *args)
            excel_path = str(Path(pdf_path).with_suffix(".xlsx"))
            if not Path(excel_path).is_file():
                raise FileNotFoundError(f"Excel export was not created: {excel_path}")
        except Exception as exc:
            messagebox.showerror("Report generation", str(exc), parent=self)
            return
        if hasattr(os, "startfile"):
            os.startfile(REPORTS_DIR if open_folder else pdf_path)
        messagebox.showinfo(
            "Report generated",
            f"PDF saved to:\n{pdf_path}\n\nExcel saved to:\n{excel_path}",
            parent=self,
        )

    def _pair_buttons(self, frame: tk.Frame, row: int, generator, args_getter) -> None:
        """Add PDF and Excel actions that invoke the same paired generator."""
        buttons = tk.Frame(frame, bg=SPLASH_BG)
        buttons.grid(row=row, column=0, pady=18)
        ttk.Button(
            buttons, text="Generate PDF",
            command=lambda: self._generate_pair(generator, args_getter(), False),
        ).pack(side="left", padx=5)
        ttk.Button(
            buttons, text="Export Excel",
            command=lambda: self._generate_pair(generator, args_getter(), True),
        ).pack(side="left", padx=5)

    def _build_daily_tab(self, frame: tk.Frame) -> None:
        """Build daily-report date controls."""
        date_var = tk.StringVar(value=today_str())
        holder = self._row(frame, "Date (DD-MM-YYYY)", 0)
        ttk.Entry(holder, textvariable=date_var, width=28).pack(side="left")
        ttk.Button(frame, text="Generate Daily PDF", command=lambda: self._generate(daily_report, date_var.get().strip())).grid(row=1, column=0, pady=18)

    def _build_monthly_tab(self, frame: tk.Frame) -> None:
        """Build monthly-report year and month controls."""
        year_var = tk.StringVar(value=str(datetime.now().year))
        month_var = tk.StringVar(value=MONTHS[datetime.now().month - 1])
        year_holder = self._row(frame, "Calendar Year", 0)
        ttk.Spinbox(year_holder, from_=2000, to=2100, textvariable=year_var, width=26).pack(side="left")
        month_holder = self._row(frame, "Month", 1)
        ttk.Combobox(month_holder, textvariable=month_var, values=MONTHS, state="readonly", width=25).pack(side="left")
        ttk.Button(frame, text="Generate Monthly PDF", command=lambda: self._generate(monthly_report, int(year_var.get()), MONTHS.index(month_var.get()) + 1)).grid(row=2, column=0, pady=18)

    def _build_class_dues_tab(self, frame: tk.Frame) -> None:
        """Build class and academic-year controls for dues reports."""
        class_var = tk.StringVar()
        year_var = tk.StringVar()
        class_holder = self._row(frame, "Class", 0)
        ttk.Combobox(class_holder, textvariable=class_var, values=self.classes, state="readonly", width=25).pack(side="left")
        if self.classes:
            class_var.set(self.classes[0])
        year_holder = self._row(frame, "Academic Year", 1)
        self._year_combo(year_holder, year_var)
        ttk.Button(frame, text="Generate Class Dues PDF", command=lambda: self._generate(classwise_dues_report, class_var.get(), year_var.get())).grid(row=2, column=0, pady=18)

    def _build_defaulter_tab(self, frame: tk.Frame) -> None:
        """Build overdue-day threshold controls."""
        days_var = tk.StringVar(value="30")
        holder = self._row(frame, "Days Threshold", 0)
        ttk.Spinbox(holder, from_=1, to=3650, textvariable=days_var, width=26).pack(side="left")
        ttk.Button(frame, text="Generate Defaulter PDF", command=lambda: self._generate(defaulter_report, int(days_var.get()))).grid(row=1, column=0, pady=18)

    def _build_year_tab(self, frame: tk.Frame, button_text: str, generator) -> None:
        """Build an academic-year-only report tab."""
        year_var = tk.StringVar()
        holder = self._row(frame, "Academic Year", 0)
        self._year_combo(holder, year_var)
        ttk.Button(frame, text=button_text, command=lambda: self._generate(generator, year_var.get())).grid(row=1, column=0, pady=18)

    def _build_feehead_tab(self, frame: tk.Frame) -> None:
        """Build academic-year and optional-month fee-head collection controls."""
        year_var = tk.StringVar()
        month_var = tk.StringVar(value="All")
        year_holder = self._row(frame, "Academic Year", 0)
        self._year_combo(year_holder, year_var)
        month_holder = self._row(frame, "Month", 1)
        ttk.Combobox(month_holder, textvariable=month_var, values=("All",) + MONTHS, state="readonly", width=25).pack(side="left")

        def args() -> tuple:
            month = None if month_var.get() == "All" else MONTHS.index(month_var.get()) + 1
            return year_var.get(), month

        self._pair_buttons(frame, 2, feehead_collection_report, args)

    def _build_comparative_tab(self, frame: tk.Frame) -> None:
        """Build calendar month controls for three-period comparison."""
        year_var = tk.StringVar(value=str(datetime.now().year))
        month_var = tk.StringVar(value=MONTHS[datetime.now().month - 1])
        year_holder = self._row(frame, "Calendar Year", 0)
        ttk.Spinbox(year_holder, from_=2000, to=2100, textvariable=year_var, width=26).pack(side="left")
        month_holder = self._row(frame, "Month", 1)
        ttk.Combobox(month_holder, textvariable=month_var, values=MONTHS, state="readonly", width=25).pack(side="left")
        self._pair_buttons(
            frame, 2, comparative_report,
            lambda: (int(year_var.get()), MONTHS.index(month_var.get()) + 1),
        )

    def _build_discount_tab(self, frame: tk.Frame) -> None:
        """Build academic-year discount-register controls."""
        year_var = tk.StringVar()
        holder = self._row(frame, "Academic Year", 0)
        self._year_combo(holder, year_var)
        self._pair_buttons(frame, 1, discount_register_report, lambda: (year_var.get(),))

    def _build_void_tab(self, frame: tk.Frame) -> None:
        """Build full immutable void-register controls."""
        tk.Label(
            frame, text="Generate the complete immutable void-payment register.",
            bg=SPLASH_BG, fg=SPLASH_FG,
        ).grid(row=0, column=0, pady=12)
        self._pair_buttons(frame, 1, void_report, lambda: ())

    def _build_audit_tab(self, frame: tk.Frame) -> None:
        """Build administrator-only audit filters."""
        user_var = tk.StringVar()
        action_var = tk.StringVar()
        table_var = tk.StringVar()
        from_var = tk.StringVar()
        to_var = tk.StringVar()
        tamper_var = tk.StringVar()

        user_holder = self._row(frame, "User", 0)
        ttk.Combobox(user_holder, textvariable=user_var, values=[""] + list(self.users), state="readonly", width=25).pack(side="left")
        for row, label, variable in ((1, "Action", action_var), (2, "Table", table_var), (3, "Date From", from_var), (4, "Date To", to_var)):
            holder = self._row(frame, label, row)
            ttk.Entry(holder, textvariable=variable, width=28).pack(side="left")
        tamper_holder = self._row(frame, "Tamper Only", 5)
        ttk.Combobox(tamper_holder, textvariable=tamper_var, values=("", "Yes", "No"), state="readonly", width=25).pack(side="left")

        def generate() -> None:
            filters = {
                "user_id": self.users.get(user_var.get(), ""),
                "action": action_var.get().strip(),
                "table_name": table_var.get().strip(),
                "date_from": from_var.get().strip(),
                "date_to": to_var.get().strip(),
            }
            if tamper_var.get():
                filters["tamper_attempt"] = 1 if tamper_var.get() == "Yes" else 0
            self._generate(audit_export, filters)

        ttk.Button(frame, text="Generate Audit PDF", command=generate).grid(row=6, column=0, pady=14)
