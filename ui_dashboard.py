"""Minimal dashboard shell opened after successful authentication."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import auth
from config import APP_TITLE, SCHOOL_NAME, SPLASH_BG, SPLASH_FG
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
        self._center_window()
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_logout_click)

    def _center_window(self) -> None:
        """Center the dashboard window on screen."""
        self.update_idletasks()
        width = int(WINDOW_SIZE.split("x")[0])
        height = int(WINDOW_SIZE.split("x")[1])
        x_position = (self.winfo_screenwidth() - width) // 2
        y_position = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{WINDOW_SIZE}+{x_position}+{y_position}")

    def _build_widgets(self) -> None:
        """Build dashboard labels and buttons."""
        tk.Label(self, text=SCHOOL_NAME, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 18, "bold")).pack(pady=(34, 8))
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
        ttk.Button(button_frame, text="Change Password", command=self._on_change_password_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Logout", command=self._on_logout_click).pack(fill="x", pady=5)

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
