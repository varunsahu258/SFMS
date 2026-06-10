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
    collection_report,
    classwise_dues_report,
    comparative_report,
    defaulter_report,
    discount_register_report,
    feehead_collection_report,
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

        self._build_daily_tab(self._tab(notebook, "Daily Report"))
        self._build_collection_tab(self._tab(notebook, "Collections"))
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

    def _collection_mode_controls(self, frame: tk.Frame, row: int) -> dict[str, tk.BooleanVar]:
        """Add the shared Cash, UPI, and Cheque report filters."""
        mode_vars = {mode: tk.BooleanVar(value=True) for mode in ("CASH", "UPI", "CHEQUE")}
        holder = self._row(frame, "Payment Modes", row)
        for mode in ("CASH", "UPI", "CHEQUE"):
            ttk.Checkbutton(holder, text=mode.title(), variable=mode_vars[mode]).pack(side="left", padx=(0, 12))
        return mode_vars

    @staticmethod
    def _selected_modes(mode_vars: dict[str, tk.BooleanVar]) -> tuple[str, ...]:
        """Return the payment modes selected in a report tab."""
        return tuple(mode for mode, variable in mode_vars.items() if variable.get())

    def _build_daily_tab(self, frame: tk.Frame) -> None:
        """Restore the dedicated clean daily collection report tab."""
        report_date = tk.StringVar(value=today_str())
        recipient = tk.StringVar()
        date_holder = self._row(frame, "Report Date (DD-MM-YYYY)", 0)
        ttk.Entry(date_holder, textvariable=report_date, width=28).pack(side="left")
        mode_vars = self._collection_mode_controls(frame, 1)
        recipient_holder = self._row(frame, "Person Collecting Report", 2)
        ttk.Entry(recipient_holder, textvariable=recipient, width=72).pack(side="left")
        tk.Label(
            frame,
            text="Enter the full name/designation and school of the person receiving the report.",
            bg=SPLASH_BG, fg=SPLASH_FG, anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            frame,
            text="Generate Daily Report PDF",
            command=lambda: self._generate(
                collection_report,
                report_date.get().strip(), report_date.get().strip(), self._selected_modes(mode_vars),
                True, False, False, False, recipient.get().strip(), "DAILY",
            ),
        ).grid(row=4, column=0, pady=18)

    def _build_collection_tab(self, frame: tk.Frame) -> None:
        """Build Today, since-last-report, and custom collection controls."""
        report_type = tk.StringVar(value="DAILY")
        from_var = tk.StringVar(value=today_str())
        to_var = tk.StringVar(value=today_str())
        recipient_var = tk.StringVar()
        include_vars = {
            "date": tk.BooleanVar(value=True),
            "receipt": tk.BooleanVar(value=False),
            "mode": tk.BooleanVar(value=False),
            "collector": tk.BooleanVar(value=False),
        }

        type_holder = self._row(frame, "Report Type", 0)
        for value, text in (
            ("DAILY", "Today's Report"),
            ("SINCE_LAST", "Transactions After Last Report"),
            ("CUSTOM", "Custom Date Range"),
        ):
            ttk.Radiobutton(type_holder, text=text, value=value, variable=report_type).pack(side="left", padx=(0, 14))

        from_holder = self._row(frame, "From Date (DD-MM-YYYY)", 1)
        from_entry = ttk.Entry(from_holder, textvariable=from_var, width=28)
        from_entry.pack(side="left")
        to_holder = self._row(frame, "To Date (DD-MM-YYYY)", 2)
        to_entry = ttk.Entry(to_holder, textvariable=to_var, width=28)
        to_entry.pack(side="left")
        mode_vars = self._collection_mode_controls(frame, 3)

        options = self._row(frame, "Optional Columns", 4)
        for key, text in (("date", "Date"), ("receipt", "Receipt No."), ("mode", "Payment Mode"), ("collector", "Collected By Account")):
            ttk.Checkbutton(options, text=text, variable=include_vars[key]).pack(side="left", padx=(0, 10))

        recipient = self._row(frame, "Person Collecting Report", 5)
        ttk.Entry(recipient, textvariable=recipient_var, width=72).pack(side="left")
        tk.Label(
            frame,
            text=("Example: Mr. L.P. Sahu, Sanskriti Vidhya Mandir High School, Bareli. "
                  "The logged-in account is recorded separately as the report generator."),
            bg=SPLASH_BG, fg=SPLASH_FG, anchor="w",
        ).grid(row=6, column=0, sticky="w", pady=(4, 0))
        tk.Label(
            frame,
            text="Every report always contains Student Name and Amount Collected; fee-head details are excluded.",
            bg=SPLASH_BG, fg=SPLASH_FG, anchor="w",
        ).grid(row=7, column=0, sticky="w", pady=(4, 0))

        def update_date_state(*_args) -> None:
            state = "normal" if report_type.get() == "CUSTOM" else "disabled"
            from_entry.configure(state=state)
            to_entry.configure(state=state)

        report_type.trace_add("write", update_date_state)
        update_date_state()

        def generate() -> None:
            kind = report_type.get()
            if kind == "DAILY":
                start = end = today_str()
            elif kind == "SINCE_LAST":
                start = end = ""
            else:
                start, end = from_var.get().strip(), to_var.get().strip()
            self._generate(
                collection_report, start, end, self._selected_modes(mode_vars),
                include_vars["date"].get(), include_vars["receipt"].get(),
                include_vars["mode"].get(), include_vars["collector"].get(),
                recipient_var.get().strip(), kind,
            )

        ttk.Button(frame, text="Generate Collection PDF", command=generate).grid(row=8, column=0, pady=18)

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
