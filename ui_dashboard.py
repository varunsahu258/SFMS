"""Minimal dashboard shell opened after successful authentication."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import auth
from config import APP_TITLE, SCHOOL_NAME, SPLASH_BG, SPLASH_FG
from ui_change_password import ChangePasswordWindow

WINDOW_SIZE = "700x460"


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
        ttk.Button(button_frame, text="Students", command=self._on_students_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Fee Heads", command=self._on_fee_heads_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Fee Structure", command=self._on_fee_structure_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Academic Years", command=self._on_academic_years_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Change Password", command=self._on_change_password_click).pack(fill="x", pady=5)
        ttk.Button(button_frame, text="Logout", command=self._on_logout_click).pack(fill="x", pady=5)

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
