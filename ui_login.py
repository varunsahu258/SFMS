"""Login window for the SFMS desktop application."""

from __future__ import annotations

import sqlite3
import threading
import tkinter as tk
from tkinter import ttk

import auth
from config import APP_TITLE, DB_PATH, SCHOOL_NAME
from utils import format_currency

ERROR_FG = "#ff6b6b"
ENTRY_WIDTH = 30
WINDOW_SIZE = "420x360"
PASSWORD_MASK = "*"


def _configured_school_name() -> str:
    """Return the school name saved in settings, with the configured fallback."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='school_name'").fetchone()
    except sqlite3.Error:
        return SCHOOL_NAME
    return str(row[0]) if row and row[0] else SCHOOL_NAME


class LoginWindow(tk.Toplevel):
    """Tkinter login window for authenticating SFMS users."""

    def __init__(self, master=None, on_dashboard_open=None):
        """Create and display the login window."""
        if master is None and tk._default_root is None:
            master = tk.Tk()
            master.withdraw()
        super().__init__(master)
        self.on_dashboard_open = on_dashboard_open
        self.password_visible = tk.BooleanVar(value=False)
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self.title(APP_TITLE)
        self.geometry(WINDOW_SIZE)
        from ui_theme import apply_theme

        self.language, self.ui_font = apply_theme(self)
        self.configure(bg=self._sfms_palette["bg"])
        self.resizable(False, False)
        self._center_window()
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.username_entry.focus_set()

    def _center_window(self) -> None:
        """Center the login window on the current screen."""
        self.update_idletasks()
        width = int(WINDOW_SIZE.split("x")[0])
        height = int(WINDOW_SIZE.split("x")[1])
        x_position = (self.winfo_screenwidth() - width) // 2
        y_position = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{WINDOW_SIZE}+{x_position}+{y_position}")

    def _build_widgets(self) -> None:
        """Build all login controls."""
        tk.Label(self, text=_configured_school_name(), bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 14, "bold")).pack(pady=(28, 4))
        tk.Label(self, text=APP_TITLE, bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], font=("Segoe UI", 30, "bold")).pack(pady=(0, 20))

        form = tk.Frame(self, bg=self._sfms_palette["bg"])
        form.pack()

        from ui_strings import label

        tk.Label(form, text=label("username", self.language), bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], anchor="w", font=(self.ui_font, 10)).grid(row=0, column=0, sticky="w")
        self.username_entry = ttk.Entry(form, textvariable=self.username_var, width=ENTRY_WIDTH)
        self.username_entry.grid(row=1, column=0, columnspan=2, pady=(2, 10), sticky="ew")

        tk.Label(form, text=label("password", self.language), bg=self._sfms_palette["bg"], fg=self._sfms_palette["fg"], anchor="w", font=(self.ui_font, 10)).grid(row=2, column=0, sticky="w")
        self.password_entry = ttk.Entry(form, textvariable=self.password_var, width=ENTRY_WIDTH, show=PASSWORD_MASK)
        self.password_entry.grid(row=3, column=0, pady=(2, 10), sticky="ew")
        ttk.Checkbutton(form, text=label("show", self.language), variable=self.password_visible, command=self._toggle_password).grid(
            row=3,
            column=1,
            padx=(8, 0),
            sticky="w",
        )

        ttk.Button(form, text=label("login", self.language), command=self._on_login_click).grid(row=4, column=0, columnspan=2, pady=(8, 8), sticky="ew")
        tk.Label(form, textvariable=self.error_var, bg=self._sfms_palette["bg"], fg=ERROR_FG, wraplength=320).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
        )

        self.bind("<Return>", self._on_enter)

    def _toggle_password(self) -> None:
        """Show or hide the password entry text."""
        auth.touch_session()
        self.password_entry.configure(show="" if self.password_visible.get() else PASSWORD_MASK)

    def _on_enter(self, _event) -> None:
        """Attempt login when Enter is pressed."""
        self._on_login_click()

    def _on_login_click(self) -> None:
        """Authenticate the entered credentials and open the dashboard on success."""
        auth.touch_session()
        success, message = auth.login(self.username_var.get(), self.password_var.get())
        if success:
            is_accountant = auth.CURRENT_SESSION is not None and auth.CURRENT_SESSION.role == "ACCOUNTANT"
            from ui_setup_wizard import SetupWizardWindow, setup_is_complete

            if not setup_is_complete():
                self.withdraw()
                SetupWizardWindow(self, on_complete=lambda: self._finish_login(is_accountant))
                return
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?",
                    (f"force_password_change_user_{auth.CURRENT_SESSION.user_id}",),
                ).fetchone()
            if row and row[0] == "1":
                from ui_users import MandatoryPasswordChangeDialog

                self.withdraw()
                MandatoryPasswordChangeDialog(self, lambda: self._finish_login(is_accountant))
                return
            self._finish_login(is_accountant)
            return
        self._show_login_error(message)

    def _finish_login(self, is_accountant: bool) -> None:
        """Close login gates and open the authenticated dashboard."""
        self.destroy()
        dashboard = self._open_dashboard()
        if is_accountant:
            self._load_accountant_dues_async(dashboard)

    def _on_close(self) -> None:
        """Run the application-level close reminder."""
        from main import on_closing

        on_closing(self)

    def _load_accountant_dues_async(self, dashboard) -> None:
        """Load today's dues off the UI thread after accountant login."""
        def worker() -> None:
            try:
                from notifications import get_todays_dues

                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA journal_mode=WAL")
                    dues = get_todays_dues(conn)
            except sqlite3.Error:
                return
            if dues:
                try:
                    dashboard.after(0, lambda: self._show_accountant_dues(dashboard, dues))
                except tk.TclError:
                    return

        threading.Thread(target=worker, daemon=True).start()

    def _show_accountant_dues(self, dashboard, dues: list[dict]) -> None:
        """Show a modal acknowledgement list of today's student dues."""
        if not dashboard.winfo_exists():
            return
        dialog = tk.Toplevel(dashboard)
        dialog.title("Today's Fee Dues")
        dialog.geometry("620x430")
        dialog.transient(dashboard)
        dialog.grab_set()
        tk.Label(dialog, text="Fees Due Today", font=("Segoe UI", 16, "bold")).pack(pady=(16, 8))
        columns = ("student", "class", "fee_head", "amount")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=13)
        for column, heading, width in (("student", "Student", 190), ("class", "Class", 100), ("fee_head", "Fee Head", 170), ("amount", "Amount", 110)):
            tree.heading(column, text=heading)
            tree.column(column, width=width)
        for due in dues:
            tree.insert("", "end", values=(due["name"], due["class"], due["fee_head"], format_currency(due["amount"])))
        tree.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Button(dialog, text="Acknowledge", command=dialog.destroy).pack(pady=(0, 14))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()

    def _show_login_error(self, message: str) -> None:
        """Display login failure details, including attempts or lock duration."""
        status = auth.get_login_status(self.username_var.get())
        minutes_remaining = int(status["minutes_remaining"] or 0)
        failed_attempts = int(status["failed_attempts"] or 0)
        if minutes_remaining:
            self.error_var.set(f"Account locked. Try after {minutes_remaining} minutes.")
        elif failed_attempts and failed_attempts < auth.MAX_FAILED_ATTEMPTS:
            remaining = auth.MAX_FAILED_ATTEMPTS - failed_attempts
            self.error_var.set(f"{message}. {remaining} attempts remaining.")
        else:
            self.error_var.set(message)

    def _open_dashboard(self) -> None:
        """Open the dashboard window and notify the startup timeout monitor."""
        from ui_dashboard import DashboardWindow

        dashboard = DashboardWindow()
        if self.on_dashboard_open is not None:
            self.on_dashboard_open()
        return dashboard
