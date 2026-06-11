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
        """Build a persistent modern sidebar, header, and single-page workspace."""
        self._nav_buttons: dict[str, tk.Button] = {}
        self._active_page = None
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
        sidebar = tk.Frame(shell, bg=palette["sidebar"], width=238)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        main = tk.Frame(shell, bg=palette["page"])
        main.pack(side="left", fill="both", expand=True)

        brand = tk.Frame(sidebar, bg=palette["sidebar"])
        brand.pack(fill="x", padx=20, pady=(24, 20))
        tk.Label(brand, text="S", bg="white", fg=palette["accent"], width=3, height=1,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        tk.Label(brand, text="  SFMS", bg=palette["sidebar"], fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side="left")

        nav_canvas = tk.Canvas(sidebar, bg=palette["sidebar"], highlightthickness=0)
        nav_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=nav_canvas.yview)
        nav_canvas.configure(yscrollcommand=nav_scroll.set)
        nav_scroll.pack(side="right", fill="y")
        nav_canvas.pack(side="left", fill="both", expand=True)
        nav = tk.Frame(nav_canvas, bg=palette["sidebar"])
        nav_window = nav_canvas.create_window((0, 0), window=nav, anchor="nw", width=220)
        nav.bind("<Configure>", lambda _event: nav_canvas.configure(scrollregion=nav_canvas.bbox("all")))
        nav_canvas.bind("<Configure>", lambda event: nav_canvas.itemconfigure(nav_window, width=event.width))

        def section(text: str) -> None:
            tk.Label(nav, text=text.upper(), bg=palette["sidebar"], fg="#c9bff0",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=22, pady=(16, 5))

        def nav_item(key: str, text: str, command, symbol: str = "•") -> None:
            button = tk.Button(
                nav, text=f" {symbol}   {text}", command=command, anchor="w", relief="flat", bd=0,
                bg=palette["sidebar"], fg="white", activebackground=palette["sidebar_hover"],
                activeforeground="white", font=("Segoe UI", 10), padx=16, pady=9, cursor="hand2",
            )
            button.pack(fill="x", padx=10, pady=2)
            self._nav_buttons[key] = button

        nav_item("dashboard", "Dashboard", self._show_dashboard, "▦")
        section("Fee Collection")
        for key, permission, text, command, symbol in (
            ("main_collection", "collect_main_fees", "Main Collection", self._on_main_collection_click, "₹"),
            ("small_collection", "collect_small_fees", "Small Collection", self._on_small_collection_click, "₹"),
            ("advance", "collect_advance_payments", "Advance Payment", self._on_advance_payment_click, "+"),
            ("dues", "view_dues", "Student Dues", self._on_dues_click, "◷"),
            ("cheques", "manage_cheques", "Cheque Management", self._on_cheques_click, "▤"),
        ):
            if auth.has_permission(permission):
                nav_item(key, text, command, symbol)
        section("School Records")
        for key, permission, text, command, symbol in (
            ("students", "manage_students", "Students", self._on_students_click, "♟"),
            ("classes", "manage_classes", "Classes & Sections", self._on_classes_click, "▥"),
            ("reports", "view_reports", "Reports", self._on_reports_click, "▧"),
            ("timetable", "view_timetable", "Timetable", self._on_timetable_click, "▦"),
            ("notices", "issue_fee_notices", "Fee Notices", self._on_fee_notices_click, "✉"),
        ):
            if auth.has_permission(permission):
                nav_item(key, text, command, symbol)
        section("Administration")
        admin_items = (
            ("discounts", "manage_discounts", "Discounts", self._on_discount_click, "%"),
            ("exemptions", "manage_exemptions", "Exemptions", self._on_exemption_click, "◇"),
            ("fee_heads", "manage_fee_heads", "Fee Heads", self._on_fee_heads_click, "≡"),
            ("fee_structure", "manage_fee_structure", "Fee Structure", self._on_fee_structure_click, "▦"),
            ("years", "manage_academic_years", "Academic Years", self._on_academic_years_click, "□"),
            ("reprint", "reprint_receipts", "Receipt Reprint", self._on_receipt_reprint_click, "↻"),
            ("void", "void_payments", "Void Payment", self._on_void_payment_click, "×"),
            ("audit", "view_audit_log", "Audit Log", self._on_audit_log_click, "⌕"),
        )
        for key, permission, text, command, symbol in admin_items:
            if auth.has_permission(permission):
                nav_item(key, text, command, symbol)
        if auth.CURRENT_SESSION is not None and auth.CURRENT_SESSION.role == "ADMIN":
            nav_item("users", "Users", self._on_users_click, "♟")
            nav_item("permissions", "Accountant Permissions", self._on_permissions_click, "✓")
            nav_item("settings", "Settings", self._on_settings_click, "⚙")
        section("Account")
        nav_item("backup", "Backup & Restore", self._open_backup_window, "⇩")
        nav_item("password", "Change Password", self._on_change_password_click, "⚿")
        nav_item("help", "Help", self._on_help_click, "?")
        nav_item("logout", "Logout", self._on_logout_click, "↪")

        header = tk.Frame(main, bg=palette["card"], height=82, highlightthickness=1,
                          highlightbackground=palette["border"])
        header.pack(fill="x")
        header.pack_propagate(False)
        title_box = tk.Frame(header, bg=palette["card"])
        title_box.pack(side="left", fill="y", padx=28)
        self.workspace_title = tk.StringVar(value="Dashboard")
        tk.Label(title_box, textvariable=self.workspace_title, bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(14, 0))
        tk.Label(title_box, text=_configured_school_name(), bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w")

        profile = tk.Frame(header, bg=palette["card"])
        profile.pack(side="right", padx=26, pady=13)
        username = auth.CURRENT_SESSION.username if auth.CURRENT_SESSION else "Guest"
        role = auth.CURRENT_SESSION.role.title() if auth.CURRENT_SESSION else "Not signed in"
        tk.Label(profile, text=username, bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="e")
        tk.Label(profile, text=role, bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 8)).pack(anchor="e")
        year_box = tk.Frame(header, bg=palette["card"])
        year_box.pack(side="right", padx=(0, 16), pady=13)
        tk.Label(year_box, text="Academic Year", bg=palette["card"], fg=palette["muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="e")
        self.academic_year_var = tk.StringVar()
        year_state = "readonly" if auth.has_permission("manage_academic_years") else "disabled"
        self.academic_year_combo = ttk.Combobox(year_box, textvariable=self.academic_year_var,
                                                state=year_state, width=15)
        self.academic_year_combo.pack(anchor="e", pady=(3, 0))
        self.academic_year_combo.bind("<<ComboboxSelected>>", self._academic_year_changed)
        self._load_academic_years()

        self.workspace = tk.Frame(main, bg=palette["page"])
        self.workspace.pack(fill="both", expand=True)
        self._show_dashboard()

    def _set_active_navigation(self, key: str) -> None:
        """Highlight the active sidebar destination."""
        palette = self._workspace_palette
        self._active_nav_key = key
        for nav_key, button in self._nav_buttons.items():
            active = nav_key == key
            button.configure(
                bg=palette["sidebar_active"] if active else palette["sidebar"],
                fg=palette["accent"] if active else "white",
                activeforeground=palette["accent"] if active else "white",
            )

    def _clear_workspace(self) -> None:
        """Remove the current page before rendering the next destination."""
        for child in self.workspace.winfo_children():
            child._workspace_navigating = True
            child.destroy()
        self._active_page = None

    def _show_workspace_page(self, page_class, title: str, key: str, *args, **kwargs):
        """Render a primary module in the dashboard instead of a new window."""
        auth.touch_session()
        self._clear_workspace()
        self.workspace_title.set(title)
        self._set_active_navigation(key)
        try:
            page = page_class(self.workspace, *args, embedded=True, **kwargs)
            page.pack(fill="both", expand=True)
            page._workspace_on_close = self._show_dashboard
            self._active_page = page
            return page
        except Exception:
            self._show_dashboard()
            raise

    def _show_dashboard(self) -> None:
        """Render the dashboard overview cards, alerts, and cashflow chart."""
        self._clear_workspace()
        self.workspace_title.set("Dashboard")
        self._set_active_navigation("dashboard")
        palette = self._workspace_palette
        page = tk.Frame(self.workspace, bg=palette["page"])
        page.pack(fill="both", expand=True, padx=24, pady=20)
        self._active_page = page

        welcome = tk.Frame(page, bg=palette["accent"], height=128)
        welcome.pack(fill="x")
        welcome.pack_propagate(False)
        username = auth.CURRENT_SESSION.username if auth.CURRENT_SESSION else "User"
        tk.Label(welcome, text=f"Hello {username},", bg=palette["accent"], fg="#ffbd68",
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=24, pady=(22, 2))
        tk.Label(welcome, text="Manage students, fees, reports and school records from one workspace.",
                 bg=palette["accent"], fg="white", font=("Segoe UI", 11)).pack(anchor="w", padx=24)

        self.notification_frame = tk.Frame(page, bg=palette["page"], height=80)
        self.notification_frame.pack(fill="x", pady=(12, 0))
        self.notification_frame.pack_propagate(False)

        stats = tk.Frame(page, bg=palette["page"])
        stats.pack(fill="x", pady=(4, 14))
        summary = self._dashboard_summary()
        for index, (label, value, color) in enumerate((
            ("Total Students", summary["students"], "#e8f1ff"),
            ("Collected Today", format(summary["today"], ",.2f"), "#e5f7ec"),
            ("Outstanding Dues", format(summary["dues"], ",.2f"), "#fff0e3"),
            ("Pending Cheques", summary["cheques"], "#f4e8ff"),
        )):
            card = tk.Frame(stats, bg=color, height=84)
            card.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 8, 0))
            card.pack_propagate(False)
            tk.Label(card, text=label, bg=color, fg=palette["muted"], font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(14, 2))
            tk.Label(card, text=str(value), bg=color, fg=palette["text"], font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=16)

        lower = tk.Frame(page, bg=palette["page"])
        lower.pack(fill="both", expand=True)
        chart_card = tk.Frame(lower, bg=palette["card"], highlightthickness=1, highlightbackground=palette["border"])
        chart_card.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(chart_card, text="Collection Overview", bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 0))
        self.cashflow_canvas = tk.Canvas(chart_card, bg=palette["card"], highlightthickness=0, height=260)
        self.cashflow_canvas.pack(fill="both", expand=True, padx=12, pady=8)
        self.cashflow_canvas.bind("<Configure>", self._draw_cashflow_chart)
        side_card = tk.Frame(lower, bg=palette["card"], width=280, highlightthickness=1,
                             highlightbackground=palette["border"])
        side_card.pack(side="right", fill="y")
        side_card.pack_propagate(False)
        tk.Label(side_card, text="Backup Status", bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        self.backup_status_var = tk.StringVar(value="Last successful backup: loading...")
        tk.Label(side_card, textvariable=self.backup_status_var, bg=palette["card"], fg=palette["muted"],
                 wraplength=240, justify="left", anchor="w").pack(fill="x", padx=18)
        ttk.Button(side_card, text="Backup Now", command=self._open_backup_window).pack(fill="x", padx=18, pady=14)
        tk.Label(side_card, text="Quick Actions", bg=palette["card"], fg=palette["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
        for text, command in (("Collect Fees", self._on_main_collection_click),
                              ("Find Student", self._on_students_click),
                              ("Generate Report", self._on_reports_click)):
            ttk.Button(side_card, text=text, command=command).pack(fill="x", padx=18, pady=4)
        self._load_notifications_async()

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

        ClassSectionWindow(self)

    def _bind_shortcuts(self) -> None:
        """Bind documented dashboard keyboard shortcuts to existing safe handlers."""
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

    @auth.require_permission("view_reports")
    def _on_reports_click(self) -> None:
        """Touch the session and open the PDF report center."""
        auth.touch_session()
        from ui_reports import ReportsWindow

        self._show_workspace_page(ReportsWindow, "Reports", "reports")

    @auth.require_permission("view_timetable")
    def _on_timetable_click(self) -> None:
        """Open the automatic timetable workspace."""
        auth.touch_session()
        from ui_timetable import TimetableWindow

        self._show_workspace_page(TimetableWindow, "Timetable", "timetable")

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


    def _on_users_click(self) -> None:
        """Open administrator user management."""
        auth.touch_session()
        from ui_users import UserManagementWindow

        self._show_workspace_page(UserManagementWindow, "User Management", "users")

    @auth.require_role("ADMIN")
    def _on_permissions_click(self) -> None:
        """Open per-accountant permission management."""
        from ui_permissions import AccountantPermissionsWindow

        self._show_workspace_page(AccountantPermissionsWindow, "Accountant Permissions", "permissions")

    @auth.require_role("ADMIN")
    def _on_permissions_click(self) -> None:
        """Open per-accountant permission management."""
        from ui_permissions import AccountantPermissionsWindow

        AccountantPermissionsWindow(self)

    def _on_settings_click(self) -> None:
        """Open administrator settings."""
        auth.touch_session()
        from ui_settings import SettingsWindow

        self._show_workspace_page(SettingsWindow, "Settings", "settings")

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
