"""Minimal dashboard shell opened after successful authentication."""

from __future__ import annotations

import sqlite3
import threading
import tkinter as tk
from tkinter import ttk

import auth
from config import APP_TITLE, DB_PATH, SCHOOL_NAME, SPLASH_BG, SPLASH_FG
from ui_change_password import ChangePasswordWindow

WINDOW_SIZE = "700x900"


class DashboardWindow(tk.Toplevel):
    """Basic dashboard window for authenticated SFMS users."""

    def __init__(self, master=None):
        """Create the dashboard shell."""
        super().__init__(master)
        self.title(f"{APP_TITLE} Dashboard")
        self.geometry(WINDOW_SIZE)
        self.configure(bg=SPLASH_BG)
        self.dismissed_notifications: set[str] = set()
        self._center_window()
        self._build_widgets()
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
        self.notification_frame = tk.Frame(self, bg=SPLASH_BG, height=82)
        self.notification_frame.pack(fill="x", padx=12, pady=(10, 0))
        self.notification_frame.pack_propagate(False)
        tk.Label(self, text=SCHOOL_NAME, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 18, "bold")).pack(pady=(12, 8))
        tk.Label(self, text=f"{APP_TITLE} Dashboard", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 26, "bold")).pack(pady=(0, 20))
        user_label = "Not signed in"
        if auth.CURRENT_SESSION is not None:
            user_label = f"Signed in as {auth.CURRENT_SESSION.username} ({auth.CURRENT_SESSION.role})"
        tk.Label(self, text=user_label, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 12)).pack(pady=(0, 20))
        button_frame = tk.Frame(self, bg=SPLASH_BG)
        button_frame.pack(pady=12)
        ttk.Button(button_frame, text="Main Collection", command=self._on_main_collection_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Small Collection", command=self._on_small_collection_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Exemption Collection", command=self._on_exemption_collection_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Advance Payment", command=self._on_advance_payment_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Dues", command=self._on_dues_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Discounts", command=self._on_discount_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Exemptions", command=self._on_exemption_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Students", command=self._on_students_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Fee Heads", command=self._on_fee_heads_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Fee Structure", command=self._on_fee_structure_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Academic Years", command=self._on_academic_years_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Reports", command=self._on_reports_click).pack(fill="x", pady=5)
        if auth.CURRENT_SESSION is not None and auth.CURRENT_SESSION.role == "ADMIN":
            ttk.Button(button_frame, text="Receipt Reprint", command=self._on_receipt_reprint_click).pack(fill="x", pady=5)
            ttk.Button(button_frame, text="Void Payment", command=self._on_void_payment_click).pack(fill="x", pady=5)
            ttk.Button(button_frame, text="Audit Log", command=self._on_audit_log_click).pack(fill="x", pady=5)
            ttk.Button(button_frame, text="Fee Notices", command=self._on_fee_notices_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Change Password", command=self._on_change_password_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Logout", command=self._on_logout_click).pack(fill="x", pady=5)

    def _load_notifications_async(self) -> None:
        """Load notification counts away from the Tk main thread."""
        def worker() -> None:
            try:
                from notifications import get_notification_state

                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA journal_mode=WAL")
                    state = get_notification_state(conn)
            except sqlite3.Error:
                return
            try:
                self.after(0, lambda: self._render_notifications(state))
            except tk.TclError:
                return

        threading.Thread(target=worker, daemon=True).start()

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

    def _banner_row(self, row: int, key: str, color: str, text: str, action_text: str, action) -> None:
        """Create one fixed-height notification banner row."""
        banner = tk.Frame(self.notification_frame, bg=color, height=38)
        banner.pack(fill="x", pady=(0, 3))
        banner.pack_propagate(False)
        tk.Label(banner, text=text, bg=color, fg="white", font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", padx=10, fill="x", expand=True)
        ttk.Button(banner, text=action_text, command=action).pack(side="right", padx=4, pady=5)
        ttk.Button(banner, text="X", width=3, command=lambda: self._dismiss_banner(key, banner)).pack(side="right", padx=(0, 6), pady=5)

    def _dismiss_banner(self, key: str, banner: tk.Frame) -> None:
        """Dismiss a notification for this in-memory login session."""
        self.dismissed_notifications.add(key)
        banner.destroy()

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

    def _on_main_collection_click(self) -> None:
        """Touch the session and open main fee collection."""
        auth.touch_session()
        from ui_collection_main import CollectionMainWindow

        CollectionMainWindow(self)

    def _on_small_collection_click(self) -> None:
        """Touch the session and open small fee collection."""
        auth.touch_session()
        from ui_collection_small import CollectionSmallWindow

        CollectionSmallWindow(self)

    def _on_exemption_collection_click(self) -> None:
        """Touch the session and open exemption-aware collection."""
        auth.touch_session()
        from ui_collection_exemption import CollectionExemptionWindow

        CollectionExemptionWindow(self)

    def _on_advance_payment_click(self) -> None:
        """Touch the session and open advance payment collection."""
        auth.touch_session()
        from ui_advance_payment import AdvancePaymentWindow

        AdvancePaymentWindow(self)

    def _on_dues_click(self) -> None:
        """Touch the session and open dues view."""
        auth.touch_session()
        from ui_dues import DuesWindow

        DuesWindow(self)

    def _on_discount_click(self) -> None:
        """Touch the session and open discount recording."""
        auth.touch_session()
        from ui_discount import DiscountWindow

        DiscountWindow(self)

    def _on_exemption_click(self) -> None:
        """Touch the session and open exemption recording."""
        auth.touch_session()
        from ui_exemption_record import ExemptionWindow

        ExemptionWindow(self)

    def _on_students_click(self) -> None:
        """Touch the session and open student management."""
        auth.touch_session()
        from ui_students import StudentWindow

        StudentWindow(self)

    def _on_fee_heads_click(self) -> None:
        """Touch the session and open fee-head management."""
        auth.touch_session()
        from ui_fee_heads import FeeHeadsWindow

        FeeHeadsWindow(self)

    def _on_fee_structure_click(self) -> None:
        """Touch the session and open fee-structure management."""
        auth.touch_session()
        from ui_fee_structure import FeeStructureWindow

        FeeStructureWindow(self)

    def _on_academic_years_click(self) -> None:
        """Touch the session and open academic-year management."""
        auth.touch_session()
        from ui_academic_year import AcademicYearWindow

        AcademicYearWindow(self)

    def _on_reports_click(self) -> None:
        """Touch the session and open the PDF report center."""
        auth.touch_session()
        from ui_reports import ReportsWindow

        ReportsWindow(self)

    def _on_receipt_reprint_click(self) -> None:
        """Touch the session and open administrator receipt reprinting."""
        auth.touch_session()
        from ui_receipt_reprint import ReprintWindow

        ReprintWindow(self)

    def _on_void_payment_click(self) -> None:
        """Touch the session and open administrator payment voiding."""
        auth.touch_session()
        from ui_void_payment import VoidPaymentWindow

        VoidPaymentWindow(self)

    def _on_audit_log_click(self) -> None:
        """Touch the session and open the administrator audit viewer."""
        auth.touch_session()
        from ui_audit import AuditLogWindow

        AuditLogWindow(self)

    def _on_fee_notices_click(self) -> None:
        """Open administrator fee-notice generation."""
        auth.touch_session()
        from ui_fee_notice import FeeNoticeWindow

        FeeNoticeWindow(self)

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
