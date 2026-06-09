"""Login window for the SFMS desktop application."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import auth
from config import APP_TITLE, SCHOOL_NAME, SPLASH_BG, SPLASH_FG

ERROR_FG = "#ff6b6b"
ENTRY_WIDTH = 30
WINDOW_SIZE = "420x360"
PASSWORD_MASK = "*"


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
        self.configure(bg=SPLASH_BG)
        self.resizable(False, False)
        self._center_window()
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
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
        tk.Label(self, text=SCHOOL_NAME, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 14, "bold")).pack(pady=(28, 4))
        tk.Label(self, text=APP_TITLE, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 30, "bold")).pack(pady=(0, 20))

        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack()

        tk.Label(form, text="Username", bg=SPLASH_BG, fg=SPLASH_FG, anchor="w").grid(row=0, column=0, sticky="w")
        self.username_entry = ttk.Entry(form, textvariable=self.username_var, width=ENTRY_WIDTH)
        self.username_entry.grid(row=1, column=0, columnspan=2, pady=(2, 10), sticky="ew")

        tk.Label(form, text="Password", bg=SPLASH_BG, fg=SPLASH_FG, anchor="w").grid(row=2, column=0, sticky="w")
        self.password_entry = ttk.Entry(form, textvariable=self.password_var, width=ENTRY_WIDTH, show=PASSWORD_MASK)
        self.password_entry.grid(row=3, column=0, pady=(2, 10), sticky="ew")
        ttk.Checkbutton(form, text="Show", variable=self.password_visible, command=self._toggle_password).grid(
            row=3,
            column=1,
            padx=(8, 0),
            sticky="w",
        )

        ttk.Button(form, text="Login", command=self._on_login_click).grid(row=4, column=0, columnspan=2, pady=(8, 8), sticky="ew")
        tk.Label(form, textvariable=self.error_var, bg=SPLASH_BG, fg=ERROR_FG, wraplength=320).grid(
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
            self.destroy()
            self._open_dashboard()
            return
        self._show_login_error(message)

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
