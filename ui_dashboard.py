"""Minimal dashboard shell opened after successful authentication."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import APP_TITLE, DB_PATH, SCHOOL_NAME
from ui_change_password import ChangePasswordWindow

WINDOW_SIZE = "1280x820"


def _parse_date(value: str | None) -> datetime | None:
    """Parse the date formats used by SFMS rows."""
    if not value:
        return None
    for date_format in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value), date_format)
        except ValueError:
            continue
    return None


def load_cashflow_summary(conn) -> tuple[str, list[tuple[str, float]]]:
    """Return active-year month labels and net collected amounts for the canvas."""
    year = conn.execute(
        "SELECT label, start_date, end_date FROM academic_years WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    if year is None:
        return "", []
    label, start_text, end_text = year[0], year[1], year[2]
    start = _parse_date(start_text)
    end = _parse_date(end_text)
    if start is None or end is None:
        return str(label or ""), []
    month_keys: list[tuple[int, int]] = []
    cursor_year, cursor_month = start.year, start.month
    while (cursor_year, cursor_month) <= (end.year, end.month):
        month_keys.append((cursor_year, cursor_month))
        if cursor_month == 12:
            cursor_year, cursor_month = cursor_year + 1, 1
        else:
            cursor_month += 1
    totals = {key: 0.0 for key in month_keys}
    for payment_date, amount_paid in conn.execute(
        "SELECT CASE WHEN UPPER(p.payment_mode)='CHEQUE' AND p.cheque_status='CLEARED' THEN p.cheque_cleared_date ELSE p.payment_date END,CASE WHEN a.allocation_type='REVERSAL' THEN -a.amount_allocated WHEN UPPER(p.payment_mode)<>'CHEQUE' OR p.cheque_status='CLEARED' THEN a.amount_allocated ELSE 0 END FROM payment_allocations a JOIN payments p ON p.id=a.payment_id JOIN student_charges c ON c.id=a.charge_id WHERE c.academic_year=?",
        (label,),
    ):
        parsed = _parse_date(payment_date)
        if parsed is not None and start <= parsed <= end:
            key = (parsed.year, parsed.month)
            if key in totals:
                totals[key] += float(amount_paid or 0)
    return str(label or ""), [
        (datetime(year_value, month_value, 1).strftime("%b"), totals[(year_value, month_value)])
        for year_value, month_value in month_keys
    ]


def _configured_school_name() -> str:
    """Return the school name saved in settings, with the configured fallback."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='school_name'").fetchone()
    except sqlite3.Error:
        return SCHOOL_NAME
    return str(row[0]) if row and row[0] else SCHOOL_NAME


class DashboardWindow(tk.Toplevel):
    """Basic dashboard window for authenticated SFMS users."""

    def __init__(self, master=None):
        """Create the dashboard shell."""
        super().__init__(master)
        self.title(f"{APP_TITLE} Dashboard")
        self.geometry(WINDOW_SIZE)
        self.dismissed_notifications: set[str] = set()
        self.cashflow_year = ""
        self.cashflow_values: list[tuple[str, float]] = []
        self._center_window()
        from ui_theme import apply_theme

        self.language, self.ui_font = apply_theme(self)
        self.configure(bg=self._sfms_palette["bg"])
        self._build_widgets()
        self._bind_shortcuts()
        self._load_notifications_async()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_window(self) -> None:
        """Center the dashboard window on screen."""
        self.update_idletasks()
        requested_width = int(WINDOW_SIZE.split("x")[0])
        requested_height = int(WINDOW_SIZE.split("x")[1])
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(requested_width, max(screen_width - 48, 900))
        height = min(requested_height, max(screen_height - 72, 620))
        x_position = max((screen_width - width) // 2, 0)
        y_position = max((screen_height - height) // 2, 0)
        self.minsize(900, 620)
        self.geometry(f"{width}x{height}+{x_position}+{y_position}")

    def _build_widgets(self) -> None:
        """Build a dashboard-first workspace without a persistent left navigation bar."""
        self._nav_buttons: dict[str, tk.Button] = {}
        self._active_page = None
        self._active_page_container = None
        self._active_scroll_canvas = None
        self._active_nav_key = "dashboard"
        palette = {
            "sidebar": "#563bb7", "sidebar_hover": "#6d52cc", "sidebar_active": "#ffffff",
            "page": "#f5f3fa", "card": "#ffffff", "text": "#201a2b", "muted": "#766f80",
            "accent": "#5b3fc0", "border": "#e9e4f2", "success": "#e4f7ea",
        }
        self._workspace_palette = palette
        self.configure(bg=palette["page"])

        shell = tk.Frame(self, bg=palette["page"])
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=palette["card"], height=86, highlightthickness=1,
                          highlightbackground=palette["border"])
        header.pack(fill="x")
        header.pack_propagate(False)

        home = tk.Button(
            header, text="SFMS  •  Home", command=self._show_dashboard, relief="flat", bd=0,
            bg=palette["accent"], fg="white", activebackground=palette["sidebar_hover"],
            activeforeground="white", font=("Segoe UI", 12, "bold"), padx=20, pady=10,
            cursor="hand2",
        )
        home.pack(side="left", padx=(18, 16), pady=17)
        self._nav_buttons["dashboard"] = home

        title_box = tk.Frame(header, bg=palette["card"])
        title_box.pack(side="left", fill="y")
        self.workspace_title = tk.StringVar(value="Dashboard")
        tk.Label(title_box, textvariable=self.workspace_title, bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(13, 0))
        tk.Label(title_box, text=_configured_school_name(), bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w")

        utilities = tk.Frame(header, bg=palette["card"])
        utilities.pack(side="right", padx=18, pady=12)
        ttk.Button(utilities, text="Backup", command=self._open_backup_window).pack(side="left", padx=3)
        ttk.Button(utilities, text="Help", command=self._on_help_click).pack(side="left", padx=3)
        ttk.Button(utilities, text="Password", command=self._on_change_password_click).pack(side="left", padx=3)
        ttk.Button(utilities, text="Logout", command=self._on_logout_click).pack(side="left", padx=3)

        profile = tk.Frame(header, bg=palette["card"])
        profile.pack(side="right", padx=(4, 12), pady=13)
        username = auth.CURRENT_SESSION.username if auth.CURRENT_SESSION else "Guest"
        role = auth.CURRENT_SESSION.role.title() if auth.CURRENT_SESSION else "Not signed in"
        tk.Label(profile, text=username, bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="e")
        tk.Label(profile, text=role, bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 8)).pack(anchor="e")

        year_box = tk.Frame(header, bg=palette["card"])
        year_box.pack(side="right", padx=(0, 8), pady=13)
        tk.Label(year_box, text="Academic Year", bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="e")
        self.academic_year_var = tk.StringVar()
        year_state = "readonly" if auth.has_permission("manage_academic_years") else "disabled"
        self.academic_year_combo = ttk.Combobox(year_box, textvariable=self.academic_year_var,
                                                state=year_state, width=15)
        self.academic_year_combo.pack(anchor="e", pady=(3, 0))
        self.academic_year_combo.bind("<<ComboboxSelected>>", self._academic_year_changed)
        self._load_academic_years()

        self.workspace = tk.Frame(shell, bg=palette["page"])
        self.workspace.pack(fill="both", expand=True)
        self._show_dashboard()

    def _set_active_navigation(self, key: str) -> None:
        """Track the active destination while navigation is driven by dashboard cards."""
        self._active_nav_key = key
        home = self._nav_buttons.get("dashboard")
        if home is not None:
            active = key == "dashboard"
            home.configure(text="SFMS  •  Home" if active else "←  Dashboard")

    def _module_groups(self) -> dict[str, dict]:
        """Return the dashboard management areas and their permission-aware actions."""
        return {
            "fees": {
                "title": "Fees Management", "icon": "₹", "color": "#5b3fc0",
                "description": "Collections, dues, receipts, fee setup, discounts and reports.",
                "items": (
                    ("main_collection", "collect_main_fees", "Main Collection", "Collect regular school fees.", self._on_main_collection_click),
                    ("small_collection", "collect_small_fees", "Small Collection", "Collect small-register fees.", self._on_small_collection_click),
                    ("advance", "collect_advance_payments", "Advance Payment", "Record future-term payments.", self._on_advance_payment_click),
                    ("dues", "view_dues", "Student Dues", "Search and print outstanding dues.", self._on_dues_click),
                    ("dues_register", "view_dues", "Dues Register", "Chronological student financial history.", self._on_dues_register_click),
                    ("cheques", "manage_cheques", "Cheque Management", "Clear, bounce or cancel cheques.", self._on_cheques_click),
                    ("receipt_history", "view_receipts", "Receipt History", "View previous receipts.", self._on_receipt_history_click),
                    ("reports", "view_reports", "Reports", "Daily, collection and dues reports.", self._on_reports_click),
                    ("notices", "issue_fee_notices", "Fee Notices", "Generate fee notices.", self._on_fee_notices_click),
                    ("fee_structure", "manage_fee_structure", "Fee Structures", "Configure fees and installments.", self._on_fee_structure_click),
                    ("opening_balances", "manage_opening_balances", "Old Opening Balances", "Import unpaid dues from manual records before SFMS.", self._on_opening_balances_click),
                    ("fee_heads", "manage_fee_heads", "Fee Heads", "Manage fee categories.", self._on_fee_heads_click),
                    ("late_fees", "apply_late_fees", "Late Fees", "Assess overdue installment charges.", self._on_late_fees_click),
                    ("discounts", "manage_discounts", "Discounts", "Apply audited fee discounts.", self._on_discount_click),
                    ("exemptions", "manage_exemptions", "Exemptions", "Record fee exemptions.", self._on_exemption_click),
                    ("exemption_collection", "collect_exemption_fees", "Exemption Collection", "Collect fees with approved exemptions.", self._on_exemption_collection_click),
                    ("years", "manage_academic_years", "Academic Years", "Create and activate academic years.", self._on_academic_years_click),
                    ("reprint", "reprint_receipts", "Receipt Reprint", "Controlled receipt reprinting.", self._on_receipt_reprint_click),
                    ("void", "void_payments", "Void Payment", "Create an audited payment reversal.", self._on_void_payment_click),
                ),
            },
            "cashbook": {
                "title": "Cashbook Management", "icon": "▤", "color": "#247a63",
                "description": "Income, expenses, balances, imports, transactions, vouchers, bills and audit reports.",
                "items": (
                    ("cashbook_expense", "manage_cashbook", "Add Expense", "Record expenses and payments.", lambda: self._on_cashbook_click("expense")),
                    ("cashbook_income", "manage_cashbook", "Add Income", "Record manual income.", lambda: self._on_cashbook_click("income")),
                    ("cashbook_balances", "view_cashbook", "View Balances", "Review account balances and vehicle expenses.", lambda: self._on_cashbook_click("balances")),
                    ("cashbook_import", "manage_cashbook", "Import Collections", "Import current or past fee receipts as income.", lambda: self._on_cashbook_click("import")),
                    ("cashbook_transactions", "view_cashbook", "View / Print Cashbook", "Filter, view and print cashbook transactions.", lambda: self._on_cashbook_click("transactions")),
                    ("cashbook_vouchers", "manage_cashbook", "Vouchers & Bills", "Create and print vouchers and bills.", lambda: self._on_cashbook_click("vouchers")),
                    ("cashbook_audit", "view_cashbook", "Audit Reports", "Review and print cashbook audit reports.", lambda: self._on_cashbook_click("audit")),
                    ("bank_statement", "manage_cashbook", "Bank Statements Upload & Analyse", "Upload Central Bank of India CSV statements and match entries.", lambda: self._on_cashbook_click("bank")),
                ),
                "planned": ("Daily Cashbook",),
            },
            "timetable": {
                "title": "Timetable Management", "icon": "▦", "color": "#3467b2",
                "description": "Teacher setup, constraints, generation, editing and timetable exports.",
                "items": (("timetable", "view_timetable", "Open Timetable", "Manage and generate the school timetable.", self._on_timetable_click),),
            },
            "students": {
                "title": "Student Management", "icon": "◉", "color": "#b45d36",
                "description": "Admissions, profiles, classes, certificates and student services.",
                "items": (
                    ("admissions", "manage_admissions", "New Admissions", "Admit students with one-time admission fees.", self._on_admissions_click),
                    ("students", "manage_students", "Student Records", "Edit profiles, imports, promotion and ID cards.", self._on_students_click),
                    ("student_tc", "manage_students", "Transfer Certificates", "Issue a TC after checking and clearing student dues.", self._on_students_click),
                    ("student_view", "view_student_details", "View Student Details", "Read-only student profile search.", self._on_student_view_click),
                    ("classes", "manage_classes", "Classes & Sections", "Maintain class and section masters.", self._on_classes_click),
                ),
                "planned": ("Conveyance Details Management",),
            },
            "exams": {
                "title": "Exam Management", "icon": "✎", "color": "#8a4f9e",
                "description": "Plan examinations, papers, seating and secure paper printing.",
                "items": (("exams", "manage_exams", "Open Exam Management", "Create exams, papers, paper storage and seating plans.", self._on_exams_click),),
                "planned": ("Exam Timetable", "Paper Management", "Exam Seating Plan", "Paper Printing"),
            },
            "results": {
                "title": "Result Management", "icon": "✓", "color": "#b08320",
                "description": "Prepare marksheets and result diaries for parent meetings.",
                "items": (("results", "manage_results", "Open Result Management", "Enter marks/grades, print marksheets and PTM diaries.", self._on_results_click),),
                "planned": ("Marksheet Generation", "Result Diary for PTMs"),
            },
        }

    def _clear_workspace(self) -> None:
        """Remove the current page before rendering the next destination."""
        if self._active_page is not None:
            self._active_page._workspace_navigating = True
        for child in self.workspace.winfo_children():
            child._workspace_navigating = True
            child.destroy()
        self._active_page = None
        self._active_page_container = None
        self._active_scroll_canvas = None

    def _workspace_section_title(self, key: str, fallback: str) -> str:
        """Return a broad section label so module pages do not repeat their own title."""
        for group in self._module_groups().values():
            if any(item[0] == key for item in group.get("items", ())):
                return group["title"]
        if key in {"backup", "audit", "users", "permissions", "settings", "password"}:
            return "Administration"
        if key in {"help", "about"}:
            return "Help & Information"
        return fallback

    def _create_workspace_canvas(self):
        """Create and activate the shared vertically scrollable workspace canvas."""
        palette = self._workspace_palette
        container = tk.Frame(self.workspace, bg=palette["page"])
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, bg=palette["page"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._active_page_container = container
        self._active_scroll_canvas = canvas
        return container, canvas

    @staticmethod
    def _mousewheel_units(event) -> int:
        """Normalize Windows, macOS, and Linux mouse-wheel events to Tk units."""
        if getattr(event, "num", None) == 4:
            return -3
        if getattr(event, "num", None) == 5:
            return 3
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return 0
        steps = max(1, abs(delta) // 120)
        return -steps if delta > 0 else steps

    def _on_workspace_mousewheel(self, event):
        """Scroll whichever dashboard or embedded module page is currently visible."""
        canvas = self._active_scroll_canvas
        if canvas is None or not canvas.winfo_exists():
            return None
        units = self._mousewheel_units(event)
        if units:
            canvas.yview_scroll(units, "units")
            return "break"
        return None

    def _show_workspace_page(self, page_class, title: str, key: str, *args, **kwargs):
        """Render a primary module in a mouse-wheel-scrollable dashboard workspace."""
        auth.touch_session()
        self._clear_workspace()
        self.workspace_title.set(self._workspace_section_title(key, title))
        self._set_active_navigation(key)
        _container, canvas = self._create_workspace_canvas()
        try:
            page = page_class(canvas, *args, embedded=True, **kwargs)
            window = canvas.create_window((0, 0), window=page, anchor="nw")
            page.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
            page._workspace_on_close = self._show_dashboard
            self._active_page = page
            return page
        except Exception:
            self._show_dashboard()
            raise

    def _scrollable_workspace_page(self):
        """Create a vertically scrollable page inside the workspace."""
        _container, canvas = self._create_workspace_canvas()
        page = tk.Frame(canvas, bg=self._workspace_palette["page"])
        window = canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        return self._active_page_container, page

    def _management_card(self, parent, column: int, group_key: str, group: dict) -> None:
        """Render one large clickable management-area card."""
        palette = self._workspace_palette
        color = group["color"]
        implemented = len(group.get("items", ()))
        status = f"{implemented} available module{'s' if implemented != 1 else ''}" if implemented else "Planned"
        card = tk.Frame(parent, bg=palette["card"], highlightthickness=1,
                        highlightbackground=palette["border"], cursor="hand2")
        card.grid(row=0, column=column, sticky="nsew", padx=7, pady=7)
        accent = tk.Frame(card, bg=color, width=9)
        accent.pack(side="left", fill="y")
        body = tk.Frame(card, bg=palette["card"], cursor="hand2")
        body.pack(side="left", fill="both", expand=True, padx=18, pady=16)
        tk.Label(body, text=group["icon"], bg=palette["card"], fg=color,
                 font=("Segoe UI", 24, "bold"), cursor="hand2").pack(anchor="w")
        tk.Label(body, text=group["title"], bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 13, "bold"), cursor="hand2").pack(anchor="w", pady=(7, 3))
        tk.Label(body, text=group["description"], bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 9), justify="left", wraplength=260,
                 cursor="hand2").pack(anchor="w")
        tk.Label(body, text=f"{status}  →", bg=palette["card"], fg=color,
                 font=("Segoe UI", 9, "bold"), cursor="hand2").pack(anchor="w", pady=(12, 0))
        command = lambda: self._show_module_group(group_key)
        for widget in (card, accent, body, *body.winfo_children()):
            widget.bind("<Button-1>", lambda _event, action=command: action())
        card.bind("<Enter>", lambda _event: card.configure(highlightbackground=color, highlightthickness=2))
        card.bind("<Leave>", lambda _event: card.configure(highlightbackground=palette["border"], highlightthickness=1))

    def _show_dashboard(self) -> None:
        """Render the management-area launcher as the application's home screen."""
        self._clear_workspace()
        self.workspace_title.set("Dashboard")
        self._set_active_navigation("dashboard")
        palette = self._workspace_palette
        container, page = self._scrollable_workspace_page()
        self._active_page = container
        content = tk.Frame(page, bg=palette["page"])
        content.pack(fill="both", expand=True, padx=26, pady=20)

        username = auth.CURRENT_SESSION.username if auth.CURRENT_SESSION else "User"
        tk.Label(content, text=f"Welcome, {username}", bg=palette["page"], fg=palette["text"],
                 font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(content, text="Choose a management area to continue.", bg=palette["page"],
                 fg=palette["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 10))

        self.notification_frame = tk.Frame(content, bg=palette["page"], height=78)
        self.notification_frame.pack(fill="x")
        self.notification_frame.pack_propagate(False)

        groups = self._module_groups()
        grid = tk.Frame(content, bg=palette["page"])
        grid.pack(fill="x", pady=(2, 10))
        keys = ("fees", "cashbook", "timetable", "students", "exams", "results")
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="management")
        for index, key in enumerate(keys):
            row_frame = grid if index < 3 else getattr(self, "_second_management_row", None)
            if index == 3:
                row_frame = tk.Frame(content, bg=palette["page"])
                row_frame.pack(fill="x", pady=(0, 10))
                for column in range(3):
                    row_frame.columnconfigure(column, weight=1, uniform="management2")
                self._second_management_row = row_frame
            elif index > 3:
                row_frame = self._second_management_row
            self._management_card(row_frame, index % 3, key, groups[key])

        summary = self._dashboard_summary()
        stats = tk.Frame(content, bg=palette["page"])
        stats.pack(fill="x", pady=(2, 12))
        for index, (label, value, color) in enumerate((
            ("Active Students", summary["students"], "#e8f1ff"),
            ("Collected Today", format(summary["today"], ",.2f"), "#e5f7ec"),
            ("Outstanding Dues", format(summary["dues"], ",.2f"), "#fff0e3"),
            ("Pending Cheques", summary["cheques"], "#f4e8ff"),
        )):
            card = tk.Frame(stats, bg=color, height=74)
            card.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 8, 0))
            card.pack_propagate(False)
            tk.Label(card, text=label, bg=color, fg=palette["muted"], font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=(11, 1))
            tk.Label(card, text=str(value), bg=color, fg=palette["text"], font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=14)

        administration = tk.LabelFrame(content, text="System & Administration", bg=palette["page"],
                                       fg=palette["text"], font=("Segoe UI", 10, "bold"), padx=10, pady=10)
        administration.pack(fill="x", pady=(0, 18))
        actions = [("About", self._on_about_click)]
        if auth.has_permission("manage_backup"):
            actions.append(("Backup & Restore", self._open_backup_window))
        if auth.has_permission("change_own_password"):
            actions.append(("Change Password", self._on_change_password_click))
        if auth.has_permission("view_help"):
            actions.append(("Help", self._on_help_click))
        if auth.has_permission("manage_academic_years"):
            actions.append(("Academic Years", self._on_academic_years_click))
        if auth.has_permission("view_audit_log"):
            actions.append(("Audit Log", self._on_audit_log_click))
        if auth.has_permission("manage_users"):
            actions.append(("Users", self._on_users_click))
        if auth.has_permission("manage_permissions"):
            actions.append(("Role Permissions", self._on_permissions_click))
        if auth.has_permission("manage_settings"):
            actions.append(("Settings", self._on_settings_click))
        for text, command in actions:
            ttk.Button(administration, text=text, command=command).pack(side="left", padx=4, pady=2)
        self._load_notifications_async()

    def _show_module_group(self, group_key: str) -> None:
        """Show the available and planned functions for one management area."""
        group = self._module_groups()[group_key]
        self._clear_workspace()
        self.workspace_title.set(group["title"])
        self._set_active_navigation(group_key)
        palette = self._workspace_palette
        container, page = self._scrollable_workspace_page()
        self._active_page = container
        content = tk.Frame(page, bg=palette["page"])
        content.pack(fill="both", expand=True, padx=28, pady=22)
        top = tk.Frame(content, bg=palette["page"])
        top.pack(fill="x", pady=(0, 10))
        ttk.Button(top, text="← Back to Dashboard", command=self._show_dashboard).pack(side="left")

        item_grid = tk.Frame(content, bg=palette["page"])
        item_grid.pack(fill="x")
        for column in range(3):
            item_grid.columnconfigure(column, weight=1, uniform=f"{group_key}_items")
        visible_items = [item for item in group.get("items", ()) if auth.has_permission(item[1])]
        for index, (_key, _permission, title, description, command) in enumerate(visible_items):
            self._module_action_card(item_grid, index, title, description, command, group["color"], False)
        start = len(visible_items)
        for offset, title in enumerate(group.get("planned", ())):
            self._module_action_card(item_grid, start + offset, title, "Planned for a future release.", None,
                                     group["color"], True)
        if not visible_items and not group.get("planned"):
            tk.Label(item_grid, text="No functions in this area are available for your account.",
                     bg=palette["page"], fg=palette["muted"], font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w", pady=20)

    def _module_action_card(self, parent, index: int, title: str, description: str, command,
                            color: str, planned: bool) -> None:
        """Render an action card within a selected management area."""
        palette = self._workspace_palette
        card = tk.Frame(parent, bg=palette["card"], highlightthickness=1,
                        highlightbackground=palette["border"], height=132)
        card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=7, pady=7)
        card.grid_propagate(False)
        tk.Label(card, text=title, bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x", padx=16, pady=(15, 3))
        tk.Label(card, text=description, bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 9), justify="left", wraplength=280, anchor="w").pack(fill="x", padx=16)
        if planned:
            tk.Label(card, text="PLANNED", bg="#eeeaf6", fg=color,
                     font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(anchor="w", padx=16, pady=(10, 0))
        else:
            ttk.Button(card, text="Open Module →", command=command, style="Accent.TButton").pack(anchor="e", padx=14, pady=(8, 10))

    def _dashboard_summary(self) -> dict[str, float | int]:
        """Load compact dashboard statistics without changing financial records."""
        result: dict[str, float | int] = {"students": 0, "today": 0.0, "dues": 0.0, "cheques": 0}
        try:
            with sqlite3.connect(DB_PATH) as conn:
                result["students"] = conn.execute("SELECT COUNT(*) FROM students WHERE COALESCE(is_active,1)=1").fetchone()[0]
                result["today"] = conn.execute(
                    "SELECT COALESCE(SUM(amount_paid),0) FROM payments WHERE payment_date=? AND COALESCE(note,'') NOT LIKE 'VOID of %'",
                    (datetime.now().strftime("%d-%m-%Y"),),
                ).fetchone()[0]
                result["dues"] = conn.execute(
                    "SELECT COALESCE(SUM(balance),0) FROM charge_ledger WHERE balance>0"
                ).fetchone()[0]
                result["cheques"] = conn.execute(
                    "SELECT COUNT(*) FROM payments WHERE UPPER(COALESCE(payment_mode,''))='CHEQUE' AND COALESCE(cheque_status,'PENDING')='PENDING'"
                ).fetchone()[0]
        except sqlite3.Error:
            pass
        return result

    def _load_academic_years(self) -> None:
        """Populate the dashboard year selector and show the current active year."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT label,is_active FROM academic_years ORDER BY start_date DESC,label DESC").fetchall()
        labels = [str(row[0]) for row in rows]
        active = next((str(row[0]) for row in rows if row[1]), labels[0] if labels else "")
        self.academic_year_combo.configure(values=labels)
        self.academic_year_var.set(active)

    def _academic_year_changed(self, _event=None) -> None:
        """Allow only an administrator to switch the application-wide academic year."""
        if not auth.has_permission("manage_academic_years"):
            messagebox.showerror(
                "Access denied",
                "You do not have permission to change the active academic year.",
                parent=self,
            )
            self._load_academic_years()
            return
        auth.touch_session()
        label = self.academic_year_var.get().strip()
        if not label:
            return
        try:
            from ledger import ensure_student_charges
            from ui_master_utils import audit, connect_db

            with connect_db() as conn:
                selected = conn.execute("SELECT id FROM academic_years WHERE label=?", (label,)).fetchone()
                if selected is None:
                    raise ValueError("The selected academic year no longer exists.")
                old = conn.execute("SELECT id,label FROM academic_years WHERE is_active=1 LIMIT 1").fetchone()
                conn.execute("UPDATE academic_years SET is_active=0")
                conn.execute("UPDATE academic_years SET is_active=1 WHERE id=?", (selected["id"],))
                ensure_student_charges(conn, label)
                audit(conn, "ACADEMIC_YEAR_SELECT", "academic_years", selected["id"], dict(old) if old else None, {"label": label})
        except Exception as exc:
            messagebox.showerror("Academic Year", str(exc), parent=self)
            self._load_academic_years()
            return
        self._load_notifications_async()

    @auth.require_permission("manage_classes")
    def _on_classes_click(self) -> None:
        auth.touch_session()
        from ui_classes import ClassSectionWindow

        self._show_workspace_page(ClassSectionWindow, "Classes & Sections", "classes")

    def _bind_shortcuts(self) -> None:
        """Bind documented dashboard keyboard shortcuts to existing safe handlers."""
        self.bind("<MouseWheel>", self._on_workspace_mousewheel, add="+")
        self.bind("<Button-4>", self._on_workspace_mousewheel, add="+")
        self.bind("<Button-5>", self._on_workspace_mousewheel, add="+")
        self.bind("<F1>", lambda _event: self._on_main_collection_click())
        self.bind("<F2>", lambda _event: self._on_dues_click())
        self.bind("<F3>", lambda _event: self._on_reports_click())
        self.bind("<F4>", lambda _event: self._on_students_click())
        self.bind("<F5>", lambda _event: self._open_backup_window())
        self.bind("<Control-l>", lambda _event: self._on_logout_click())
        self.bind("<Control-p>", lambda _event: self._on_receipt_reprint_click())
        self.bind("<Escape>", self._close_focused_child)

    def _close_focused_child(self, _event=None) -> None:
        """Close the focused child window without accidentally closing the dashboard."""
        auth.touch_session()
        focused = self.focus_get()
        if focused is not None:
            top = focused.winfo_toplevel()
            if top is not self:
                top.destroy()

    def _load_notifications_async(self) -> None:
        """Load notification counts away from the Tk main thread."""
        def worker() -> None:
            try:
                from notifications import get_notification_state

                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA journal_mode=WAL")
                    state = get_notification_state(conn)
                    cashflow_year, cashflow_values = load_cashflow_summary(conn)
                    from backup import backup_status

                    state["backup_status"] = backup_status(conn)
            except sqlite3.Error:
                return
            try:
                self.after(0, lambda: self._apply_dashboard_data(state, cashflow_year, cashflow_values))
            except tk.TclError:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _apply_dashboard_data(self, state: dict, year_label: str, values: list[tuple[str, float]]) -> None:
        """Apply worker-loaded notifications and chart data on Tk's main thread."""
        self.cashflow_year = year_label
        self.cashflow_values = values
        if self._active_nav_key == "dashboard":
            self._render_notifications(state)
            self._render_backup_status(state.get("backup_status", {}))
            self._draw_cashflow_chart()

    def _render_backup_status(self, status: dict) -> None:
        """Render last successful backup and consecutive failure count."""
        last_success = status.get("last_successful_backup_at") or "Never"
        failures = int(status.get("consecutive_backup_failures") or 0)
        text = f"Last successful backup: {last_success}"
        if failures > 0:
            text += f"\nConsecutive failures: {failures}"
        if hasattr(self, "backup_status_var"):
            self.backup_status_var.set(text)

    def show_backup_failure_warning(self, failures: int) -> None:
        """Show a non-dismissible repeated-backup-failure banner."""
        self._banner_row(
            0, "backup_failure", "#b00020",
            f"Automatic backups have failed {failures} consecutive times — run Backup Now.",
            "Backup Now", self._open_backup_window, dismissible=False,
        )

    def _draw_cashflow_chart(self, _event=None) -> None:
        """Draw the active academic year's responsive monthly cashflow bars."""
        if not hasattr(self, "cashflow_canvas"):
            return
        canvas = self.cashflow_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 180)
        canvas.create_text(
            width / 2, 14, text=f"Collection — {self.cashflow_year or 'Academic Year'}",
            font=("Segoe UI", 10, "bold"), fill="#111111",
        )
        left, right, top, bottom = 34, width - 8, 36, height - 28
        canvas.create_line(left, top, left, bottom, fill="#333333")
        canvas.create_line(left, bottom, right, bottom, fill="#333333")
        values = self.cashflow_values
        if not values:
            canvas.create_text(width / 2, height / 2, text="No collection data", fill="#666666")
            return
        maximum = max(max(amount, 0) for _month, amount in values) or 1.0
        slot = max((right - left) / len(values), 1)
        bar_width = max(min(slot * 0.58, 24), 4)
        plot_height = max(bottom - top - 14, 1)
        for index, (month, amount) in enumerate(values):
            x_center = left + slot * (index + 0.5)
            positive_amount = max(amount, 0)
            bar_height = plot_height * positive_amount / maximum
            y_top = bottom - bar_height
            canvas.create_rectangle(
                x_center - bar_width / 2, y_top, x_center + bar_width / 2, bottom,
                fill="#1a1a5e", outline="#1a1a5e",
            )
            amount_label = f"{amount / 1000:.1f}k" if abs(amount) >= 1000 else f"{amount:.0f}"
            canvas.create_text(x_center, max(y_top - 7, top), text=amount_label, font=("Segoe UI", 6), fill="#222222")
            canvas.create_text(x_center, bottom + 10, text=month, font=("Segoe UI", 7), fill="#222222")

    def _render_notifications(self, state: dict) -> None:
        """Render priority dues and backup banners inside the reserved frame."""
        if not self.winfo_exists() or self._active_nav_key != "dashboard":
            return
        try:
            children = self.notification_frame.winfo_children()
        except tk.TclError:
            return
        for child in children:
            child.destroy()
        overdue = None
        for threshold, key, color in ((90, "overdue_90", "#b00020"), (60, "overdue_60", "#d2691e"), (30, "overdue_30", "#d4a900")):
            if state.get(key, 0) and key not in self.dismissed_notifications:
                overdue = (threshold, key, color, int(state[key]))
                break
        row = 0
        if overdue:
            threshold, key, color, count = overdue
            self._banner_row(
                row, key, color,
                f"{count} students have fees overdue {threshold}+ days",
                "View", lambda value=threshold: self._open_threshold_dues(value),
            )
            row += 1
        if state.get("backup_overdue") and "backup" not in self.dismissed_notifications:
            self._banner_row(
                row, "backup", "#1f5f99",
                "Backup overdue — Take backup now.",
                "Backup Now", self._open_backup_window,
            )

    def _banner_row(self, row: int, key: str, color: str, text: str, action_text: str, action, dismissible: bool = True) -> None:
        """Create one fixed-height notification banner row."""
        banner = tk.Frame(self.notification_frame, bg=color, height=38)
        banner.pack(fill="x", pady=(0, 3))
        banner.pack_propagate(False)
        tk.Label(banner, text=text, bg=color, fg="white", font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", padx=10, fill="x", expand=True)
        ttk.Button(banner, text=action_text, command=action).pack(side="right", padx=4, pady=5)
        if dismissible:
            ttk.Button(banner, text="X", width=3, command=lambda: self._dismiss_banner(key, banner)).pack(side="right", padx=(0, 6), pady=5)

    def _dismiss_banner(self, key: str, banner: tk.Frame) -> None:
        """Dismiss a notification for this in-memory login session."""
        auth.touch_session()
        self.dismissed_notifications.add(key)
        banner.destroy()

    @auth.require_permission("view_dues")
    def _open_threshold_dues(self, threshold: int) -> None:
        """Open dues filtered to the selected overdue threshold."""
        auth.touch_session()
        from ui_dues import DuesWindow

        self._show_workspace_page(DuesWindow, "Student Dues", "dues", overdue_threshold=threshold)

    @auth.require_permission("manage_backup")
    def _open_backup_window(self) -> None:
        """Open the manual backup window."""
        auth.touch_session()
        from ui_backup import BackupWindow

        self._show_workspace_page(BackupWindow, "Backup & Restore", "backup")

    def _on_close(self) -> None:
        """Run the application-level close reminder."""
        from main import on_closing

        on_closing(self)

    @auth.require_permission("collect_main_fees")
    def _on_main_collection_click(self) -> None:
        """Touch the session and open main fee collection."""
        auth.touch_session()
        from ui_collection_main import CollectionMainWindow

        self._show_workspace_page(CollectionMainWindow, "Main Fee Collection", "main_collection")

    @auth.require_permission("collect_small_fees")
    def _on_small_collection_click(self) -> None:
        """Touch the session and open small fee collection."""
        auth.touch_session()
        from ui_collection_small import CollectionSmallWindow

        self._show_workspace_page(CollectionSmallWindow, "Small Fee Collection", "small_collection")

    @auth.require_permission("collect_exemption_fees")
    def _on_exemption_collection_click(self) -> None:
        """Touch the session and open exemption-aware collection."""
        auth.touch_session()
        from ui_collection_exemption import CollectionExemptionWindow

        self._show_workspace_page(CollectionExemptionWindow, "Exemption Collection", "exemption_collection")

    @auth.require_permission("collect_advance_payments")
    def _on_advance_payment_click(self) -> None:
        """Touch the session and open advance payment collection."""
        auth.touch_session()
        from ui_advance_payment import AdvancePaymentWindow

        self._show_workspace_page(AdvancePaymentWindow, "Advance Payment", "advance")

    @auth.require_permission("view_dues")
    def _on_dues_click(self) -> None:
        """Touch the session and open dues view."""
        auth.touch_session()
        from ui_dues import DuesWindow

        self._show_workspace_page(DuesWindow, "Student Dues", "dues")

    @auth.require_permission("manage_opening_balances")
    def _on_opening_balances_click(self) -> None:
        """Open audited prior-year balance migration."""
        auth.touch_session()
        from ui_opening_balances import OpeningBalanceWindow

        self._show_workspace_page(OpeningBalanceWindow, "Old Opening Balances", "opening_balances")

    @auth.require_permission("manage_discounts")
    def _on_discount_click(self) -> None:
        """Touch the session and open discount recording."""
        auth.touch_session()
        from ui_discount import DiscountWindow

        self._show_workspace_page(DiscountWindow, "Discounts", "discounts")

    @auth.require_permission("manage_exemptions")
    def _on_exemption_click(self) -> None:
        """Touch the session and open exemption recording."""
        auth.touch_session()
        from ui_exemption_record import ExemptionWindow

        self._show_workspace_page(ExemptionWindow, "Exemptions", "exemptions")

    @auth.require_permission("manage_admissions")
    def _on_admissions_click(self) -> None:
        """Open the dedicated new-admission workflow."""
        auth.touch_session()
        from ui_admissions import AdmissionsWindow

        self._show_workspace_page(AdmissionsWindow, "New Admissions", "admissions")

    @auth.require_permission("view_dues")
    def _on_dues_register_click(self) -> None:
        """Open the chronological student dues register."""
        auth.touch_session()
        from ui_dues_register import DuesRegisterWindow

        self._show_workspace_page(DuesRegisterWindow, "Dues Register", "dues_register")

    @auth.require_permission("manage_students")
    def _on_students_click(self) -> None:
        """Touch the session and open student management."""
        auth.touch_session()
        from ui_students import StudentWindow

        self._show_workspace_page(StudentWindow, "Students", "students")

    @auth.require_permission("manage_fee_heads")
    def _on_fee_heads_click(self) -> None:
        """Touch the session and open fee-head management."""
        auth.touch_session()
        from ui_fee_heads import FeeHeadsWindow

        self._show_workspace_page(FeeHeadsWindow, "Fee Heads", "fee_heads")

    @auth.require_permission("manage_fee_structure")
    def _on_fee_structure_click(self) -> None:
        """Touch the session and open fee-structure management."""
        auth.touch_session()
        from ui_fee_structure import FeeStructureWindow

        self._show_workspace_page(FeeStructureWindow, "Fee Structure", "fee_structure")

    @auth.require_permission("manage_academic_years")
    def _on_academic_years_click(self) -> None:
        """Touch the session and open academic-year management."""
        auth.touch_session()
        from ui_academic_year import AcademicYearWindow

        self._show_workspace_page(AcademicYearWindow, "Academic Years", "years")

    @auth.require_permission("view_student_details")
    def _on_student_view_click(self) -> None:
        """Open the read-only student profile search."""
        auth.touch_session()
        from ui_student_view import StudentViewWindow
        self._show_workspace_page(StudentViewWindow, "View Student Details", "student_view")

    @auth.require_permission("view_receipts")
    def _on_receipt_history_click(self) -> None:
        """Open read-only receipt history."""
        auth.touch_session()
        from ui_receipt_history import ReceiptHistoryWindow
        self._show_workspace_page(ReceiptHistoryWindow, "Receipt History", "receipt_history")

    @auth.require_permission("apply_late_fees")
    def _on_late_fees_click(self) -> None:
        """Open selective late-fee assessment."""
        auth.touch_session()
        from ui_late_fees import LateFeeWindow
        self._show_workspace_page(LateFeeWindow, "Apply Late Fees", "late_fees")

    @auth.require_permission("view_reports")
    def _on_reports_click(self) -> None:
        """Touch the session and open the PDF report center."""
        auth.touch_session()
        from ui_reports import ReportsWindow

        self._show_workspace_page(ReportsWindow, "Reports", "reports")

    @auth.require_permission("view_cashbook")
    def _on_cashbook_click(self, initial_tab: str = "transactions") -> None:
        """Open cashbook management, reports, vouchers, and bank analysis."""
        auth.touch_session()
        from ui_cashbook import CashbookWindow

        self._show_workspace_page(lambda master, embedded=False: CashbookWindow(master, embedded=embedded, initial_tab=initial_tab), "Cashbook", "cashbook")

    @auth.require_permission("view_timetable")
    def _on_timetable_click(self) -> None:
        """Open the automatic timetable workspace."""
        auth.touch_session()
        from ui_timetable import TimetableWindow

        self._show_workspace_page(TimetableWindow, "Timetable", "timetable")


    @auth.require_permission("manage_exams")
    def _on_exams_click(self) -> None:
        """Open complete exam planning, paper, and seating management."""
        auth.touch_session()
        from ui_exam import ExamWindow

        self._show_workspace_page(ExamWindow, "Exam Management", "exams")

    @auth.require_permission("manage_results")
    def _on_results_click(self) -> None:
        """Open marks entry, marksheet printing, and PTM result diaries."""
        auth.touch_session()
        from ui_result import ResultWindow

        self._show_workspace_page(ResultWindow, "Result Management", "results")

    @auth.require_permission("reprint_receipts")
    def _on_receipt_reprint_click(self) -> None:
        """Touch the session and open administrator receipt reprinting."""
        auth.touch_session()
        from ui_receipt_reprint import ReprintWindow

        self._show_workspace_page(ReprintWindow, "Receipt Reprint", "reprint")

    @auth.require_permission("void_payments")
    def _on_void_payment_click(self) -> None:
        """Touch the session and open administrator payment voiding."""
        auth.touch_session()
        from ui_void_payment import VoidPaymentWindow

        self._show_workspace_page(VoidPaymentWindow, "Void Payment", "void")

    @auth.require_permission("manage_cheques")
    def _on_cheques_click(self) -> None:
        """Open the administrator cheque lifecycle screen."""
        auth.touch_session()
        from ui_cheques import ChequeManagementWindow

        self._show_workspace_page(ChequeManagementWindow, "Cheque Management", "cheques")

    @auth.require_permission("view_audit_log")
    def _on_audit_log_click(self) -> None:
        """Touch the session and open the administrator audit viewer."""
        auth.touch_session()
        from ui_audit import AuditLogWindow

        self._show_workspace_page(AuditLogWindow, "Audit Log", "audit")

    @auth.require_permission("issue_fee_notices")
    def _on_fee_notices_click(self) -> None:
        """Open administrator fee-notice generation."""
        auth.touch_session()
        from ui_fee_notice import FeeNoticeWindow

        self._show_workspace_page(FeeNoticeWindow, "Fee Notices", "notices")


    @auth.require_permission("manage_users")
    def _on_users_click(self) -> None:
        """Open administrator user management."""
        auth.touch_session()
        from ui_users import UserManagementWindow

        self._show_workspace_page(UserManagementWindow, "User Management", "users")

    @auth.require_permission("manage_permissions")
    def _on_permissions_click(self) -> None:
        """Open configurable role/module permission management."""
        auth.touch_session()
        from ui_permissions import AccountantPermissionsWindow

        self._show_workspace_page(AccountantPermissionsWindow, "Accountant Permissions", "permissions")

    @auth.require_permission("manage_settings")
    def _on_settings_click(self) -> None:
        """Open administrator settings."""
        auth.touch_session()
        from ui_settings import SettingsWindow

        self._show_workspace_page(SettingsWindow, "Settings", "settings")

    @auth.require_permission("view_help")
    def _on_help_click(self) -> None:
        """Open bundled offline help."""
        auth.touch_session()
        from ui_help import HelpWindow

        self._show_workspace_page(HelpWindow, "Help", "help")

    def _on_about_click(self) -> None:
        """Open application information."""
        auth.touch_session()
        from ui_about import AboutDialog

        AboutDialog(self)

    @auth.require_permission("change_own_password")
    def _on_change_password_click(self) -> None:
        """Touch the session and open the change-password window."""
        auth.touch_session()
        self._show_workspace_page(ChangePasswordWindow, "Change Password", "password")

    def _on_logout_click(self) -> None:
        """Touch the session, log out, close the dashboard, and open login."""
        auth.touch_session()
        auth.logout()
        self.destroy()
        from ui_login import LoginWindow

        LoginWindow()
