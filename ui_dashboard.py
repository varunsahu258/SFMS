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

WINDOW_SIZE = "700x900"


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
        width = int(WINDOW_SIZE.split("x")[0])
        height = int(WINDOW_SIZE.split("x")[1])
        x_position = (self.winfo_screenwidth() - width) // 2
        y_position = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{WINDOW_SIZE}+{x_position}+{y_position}")

    def _build_widgets(self) -> None:
        """Build the fixed notification area, dashboard labels, and buttons."""
        from ui_strings import label

        self.notification_frame = tk.Frame(self, bg=self._sfms_palette["bg"], height=82)
        self.notification_frame.pack(fill="x", padx=12, pady=(10, 0))
        self.notification_frame.pack_propagate(False)
        header = tk.Frame(self, bg=self._sfms_palette["bg"])
        header.pack(fill="x", padx=18, pady=(12, 8))
        title_box = tk.Frame(header, bg=self._sfms_palette["bg"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text=_configured_school_name(), bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(title_box, text=f"{APP_TITLE} Dashboard", bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 26, "bold")).pack(anchor="w")
        year_box = tk.Frame(header, bg=self._sfms_palette["bg"])
        year_box.pack(side="right", anchor="ne", padx=(12, 0))
        tk.Label(year_box, text="Academic Year", bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 10, "bold")).pack(anchor="e")
        self.academic_year_var = tk.StringVar()
        is_admin = auth.CURRENT_SESSION is not None and auth.CURRENT_SESSION.role == "ADMIN"
        year_state = "readonly" if auth.has_permission("manage_academic_years") else "disabled"
        self.academic_year_combo = ttk.Combobox(year_box, textvariable=self.academic_year_var, state=year_state, width=16)
        self.academic_year_combo.pack(anchor="e", pady=(4, 0))
        self.academic_year_combo.bind("<<ComboboxSelected>>", self._academic_year_changed)
        self._load_academic_years()
        user_label = "Not signed in"
        if auth.CURRENT_SESSION is not None:
            user_label = f"Signed in as {auth.CURRENT_SESSION.username} ({auth.CURRENT_SESSION.role})"
        tk.Label(self, text=user_label, bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 12)).pack(pady=(0, 20))
        content_frame = tk.Frame(self, bg=self._sfms_palette["bg"])
        content_frame.pack(fill="both", expand=True, padx=18, pady=8)
        button_column = tk.Frame(content_frame, bg=self._sfms_palette["bg"])
        button_column.pack(side="left", fill="y", padx=(20, 12))
        button_canvas = tk.Canvas(button_column, width=230, bg=self._sfms_palette["bg"], highlightthickness=0)
        button_scroll = ttk.Scrollbar(button_column, orient="vertical", command=button_canvas.yview)
        button_canvas.configure(yscrollcommand=button_scroll.set)
        button_scroll.pack(side="right", fill="y")
        button_canvas.pack(side="left", fill="y", expand=True)
        button_frame = tk.Frame(button_canvas, bg=self._sfms_palette["bg"])
        button_window = button_canvas.create_window((0, 0), window=button_frame, anchor="nw", width=220)
        button_frame.bind("<Configure>", lambda _event: button_canvas.configure(scrollregion=button_canvas.bbox("all")))
        button_canvas.bind("<Configure>", lambda event: button_canvas.itemconfigure(button_window, width=max(event.width - 4, 180)))
        chart_frame = tk.Frame(content_frame, bg=self._sfms_palette["bg"])
        chart_frame.pack(side="right", fill="both", expand=True, padx=(10, 16), pady=8)
        self.cashflow_canvas = tk.Canvas(
            chart_frame, width=300, height=180, bg="white",
            highlightthickness=1, highlightbackground="#777777",
        )
        self.cashflow_canvas.pack(fill="both", expand=True)
        self.cashflow_canvas.bind("<Configure>", self._draw_cashflow_chart)
        self.backup_status_frame = tk.LabelFrame(
            chart_frame, text="Backup Status", bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], padx=10, pady=8
        )
        self.backup_status_frame.pack(fill="x", pady=(10, 0))
        self.backup_status_var = tk.StringVar(value="Last successful backup: loading...")
        tk.Label(
            self.backup_status_frame, textvariable=self.backup_status_var, bg=self._sfms_palette["bg"],
            fg=self._sfms_palette["fg"], justify="left", anchor="w"
        ).pack(side="left", fill="x", expand=True)
        if is_admin:
            ttk.Button(self.backup_status_frame, text="Backup Now", command=self._open_backup_window).pack(side="right")
        permission_buttons = (
            ("collect_main_fees", label("main_collection", self.language), self._on_main_collection_click),
            ("collect_small_fees", label("small_collection", self.language), self._on_small_collection_click),
            ("collect_exemption_fees", label("exemption_collection", self.language), self._on_exemption_collection_click),
            ("collect_advance_payments", label("advance_payment", self.language), self._on_advance_payment_click),
            ("view_dues", label("dues", self.language), self._on_dues_click),
            ("manage_students", label("students", self.language), self._on_students_click),
            ("manage_classes", "Classes and Sections", self._on_classes_click),
            ("view_reports", label("reports", self.language), self._on_reports_click),
            ("manage_discounts", label("discounts", self.language), self._on_discount_click),
            ("manage_exemptions", label("exemptions", self.language), self._on_exemption_click),
            ("manage_fee_heads", label("fee_heads", self.language), self._on_fee_heads_click),
            ("manage_fee_structure", label("fee_structure", self.language), self._on_fee_structure_click),
            ("manage_academic_years", label("academic_years", self.language), self._on_academic_years_click),
            ("reprint_receipts", label("receipt_reprint", self.language), self._on_receipt_reprint_click),
            ("void_payments", label("void_payment", self.language), self._on_void_payment_click),
            ("manage_cheques", "Cheque Management", self._on_cheques_click),
            ("view_audit_log", label("audit_log", self.language), self._on_audit_log_click),
            ("issue_fee_notices", label("fee_notices", self.language), self._on_fee_notices_click),
        )
        for permission_key, text, command in permission_buttons:
            if auth.has_permission(permission_key):
                ttk.Button(button_frame, text=text, command=command).pack(fill="x", pady=5)
        if is_admin:
            ttk.Button(button_frame, text=label("user_management", self.language), command=self._on_users_click).pack(fill="x", pady=5)
            ttk.Button(button_frame, text="Accountant Permissions", command=self._on_permissions_click).pack(fill="x", pady=5)
            ttk.Button(button_frame, text=label("settings", self.language), command=self._on_settings_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text=label("help", self.language), command=self._on_help_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text=label("about", self.language), command=self._on_about_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text=label("change_password", self.language), command=self._on_change_password_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text=label("logout", self.language), command=self._on_logout_click).pack(fill="x", pady=5)

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
        if not self.winfo_exists():
            return
        for child in self.notification_frame.winfo_children():
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

        DuesWindow(self, overdue_threshold=threshold)

    def _open_backup_window(self) -> None:
        """Open the manual backup window."""
        auth.touch_session()
        from ui_backup import BackupWindow

        backup_window = BackupWindow(self)
        self.wait_window(backup_window)
        self._load_notifications_async()

    def _on_close(self) -> None:
        """Run the application-level close reminder."""
        from main import on_closing

        on_closing(self)

    @auth.require_permission("collect_main_fees")
    def _on_main_collection_click(self) -> None:
        """Touch the session and open main fee collection."""
        auth.touch_session()
        from ui_collection_main import CollectionMainWindow

        CollectionMainWindow(self)

    @auth.require_permission("collect_small_fees")
    def _on_small_collection_click(self) -> None:
        """Touch the session and open small fee collection."""
        auth.touch_session()
        from ui_collection_small import CollectionSmallWindow

        CollectionSmallWindow(self)

    @auth.require_permission("collect_exemption_fees")
    def _on_exemption_collection_click(self) -> None:
        """Touch the session and open exemption-aware collection."""
        auth.touch_session()
        from ui_collection_exemption import CollectionExemptionWindow

        CollectionExemptionWindow(self)

    @auth.require_permission("collect_advance_payments")
    def _on_advance_payment_click(self) -> None:
        """Touch the session and open advance payment collection."""
        auth.touch_session()
        from ui_advance_payment import AdvancePaymentWindow

        AdvancePaymentWindow(self)

    @auth.require_permission("view_dues")
    def _on_dues_click(self) -> None:
        """Touch the session and open dues view."""
        auth.touch_session()
        from ui_dues import DuesWindow

        DuesWindow(self)

    @auth.require_permission("manage_discounts")
    def _on_discount_click(self) -> None:
        """Touch the session and open discount recording."""
        auth.touch_session()
        from ui_discount import DiscountWindow

        DiscountWindow(self)

    @auth.require_permission("manage_exemptions")
    def _on_exemption_click(self) -> None:
        """Touch the session and open exemption recording."""
        auth.touch_session()
        from ui_exemption_record import ExemptionWindow

        ExemptionWindow(self)

    @auth.require_permission("manage_students")
    def _on_students_click(self) -> None:
        """Touch the session and open student management."""
        auth.touch_session()
        from ui_students import StudentWindow

        StudentWindow(self)

    @auth.require_permission("manage_fee_heads")
    def _on_fee_heads_click(self) -> None:
        """Touch the session and open fee-head management."""
        auth.touch_session()
        from ui_fee_heads import FeeHeadsWindow

        FeeHeadsWindow(self)

    @auth.require_permission("manage_fee_structure")
    def _on_fee_structure_click(self) -> None:
        """Touch the session and open fee-structure management."""
        auth.touch_session()
        from ui_fee_structure import FeeStructureWindow

        FeeStructureWindow(self)

    @auth.require_permission("manage_academic_years")
    def _on_academic_years_click(self) -> None:
        """Touch the session and open academic-year management."""
        auth.touch_session()
        from ui_academic_year import AcademicYearWindow

        AcademicYearWindow(self)

    @auth.require_permission("view_reports")
    def _on_reports_click(self) -> None:
        """Touch the session and open the PDF report center."""
        auth.touch_session()
        from ui_reports import ReportsWindow

        ReportsWindow(self)

    @auth.require_permission("reprint_receipts")
    def _on_receipt_reprint_click(self) -> None:
        """Touch the session and open administrator receipt reprinting."""
        auth.touch_session()
        from ui_receipt_reprint import ReprintWindow

        ReprintWindow(self)

    @auth.require_permission("void_payments")
    def _on_void_payment_click(self) -> None:
        """Touch the session and open administrator payment voiding."""
        auth.touch_session()
        from ui_void_payment import VoidPaymentWindow

        VoidPaymentWindow(self)

    @auth.require_permission("manage_cheques")
    def _on_cheques_click(self) -> None:
        """Open the administrator cheque lifecycle screen."""
        auth.touch_session()
        from ui_cheques import ChequeManagementWindow

        ChequeManagementWindow(self)

    @auth.require_permission("view_audit_log")
    def _on_audit_log_click(self) -> None:
        """Touch the session and open the administrator audit viewer."""
        auth.touch_session()
        from ui_audit import AuditLogWindow

        AuditLogWindow(self)

    @auth.require_permission("issue_fee_notices")
    def _on_fee_notices_click(self) -> None:
        """Open administrator fee-notice generation."""
        auth.touch_session()
        from ui_fee_notice import FeeNoticeWindow

        FeeNoticeWindow(self)


    def _on_users_click(self) -> None:
        """Open administrator user management."""
        auth.touch_session()
        from ui_users import UserManagementWindow

        UserManagementWindow(self)

    @auth.require_role("ADMIN")
    def _on_permissions_click(self) -> None:
        """Open per-accountant permission management."""
        from ui_permissions import AccountantPermissionsWindow

        AccountantPermissionsWindow(self)

    def _on_settings_click(self) -> None:
        """Open administrator settings."""
        auth.touch_session()
        from ui_settings import SettingsWindow

        SettingsWindow(self)

    def _on_help_click(self) -> None:
        """Open bundled offline help."""
        auth.touch_session()
        from ui_help import HelpWindow

        HelpWindow(self)

    def _on_about_click(self) -> None:
        """Open application information."""
        auth.touch_session()
        from ui_about import AboutDialog

        AboutDialog(self)

    def _on_change_password_click(self) -> None:
        """Touch the session and open the change-password window."""
        auth.touch_session()
        ChangePasswordWindow(self)

    def _on_logout_click(self) -> None:
        """Touch the session, log out, close the dashboard, and open login."""
        auth.touch_session()
        auth.logout()
        self.destroy()
        from ui_login import LoginWindow

        LoginWindow()
