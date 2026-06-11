"""Mandatory first-run setup wizard for SFMS."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import bcrypt
from PIL import Image, ImageTk

import auth
from audit import log_action
from config import DB_PATH
from ui_theme import apply_theme
from ui_date import DateEntry


def setup_is_complete() -> bool:
    """Return whether the mandatory initial configuration has finished."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()
    return bool(row and row[0] == "1")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class SetupWizardWindow(tk.Toplevel):
    """Collect mandatory first-run administrator and school configuration."""

    def __init__(self, master=None, on_complete=None):
        if auth.CURRENT_SESSION is None or auth.CURRENT_SESSION.role != "ADMIN":
            raise PermissionError("Initial setup requires an authenticated administrator.")
        super().__init__(master)
        apply_theme(self)
        self.on_complete = on_complete
        self.title("SFMS First-Run Setup")
        self.geometry("760x620")
        self.minsize(680, 540)
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.step = 0
        self.password = tk.StringVar()
        self.confirm_password = tk.StringVar()
        self.school_name = tk.StringVar()
        self.school_address = tk.StringVar()
        self.logo_path = tk.StringVar()
        self.year_label = tk.StringVar(value=f"{datetime.now().year}-{str(datetime.now().year + 1)[-2:]}")
        self.start_date = tk.StringVar(value=f"01-04-{datetime.now().year}")
        self.end_date = tk.StringVar(value=f"31-03-{datetime.now().year + 1}")
        self.logo_image = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content = ttk.Frame(self, padding=(28, 24, 28, 12))
        self.content.grid(row=0, column=0, sticky="nsew")

        # Keep navigation in a fixed grid row.  Previously the expanding content
        # frame could consume the fixed-height window and push Next/Finish below it.
        self.navigation = ttk.Frame(self, padding=(28, 12, 28, 22))
        self.navigation.grid(row=1, column=0, sticky="ew")
        self.navigation.columnconfigure(1, weight=1)
        self.back_button = ttk.Button(self.navigation, text="Back", command=self.back)
        self.back_button.grid(row=0, column=0, sticky="w")
        self.step_label = ttk.Label(self.navigation, text="", style="Muted.TLabel")
        self.step_label.grid(row=0, column=1)
        self.next_button = ttk.Button(
            self.navigation, text="Next", command=self.next, style="Accent.TButton"
        )
        self.next_button.grid(row=0, column=2, sticky="e")
        self.bind("<Return>", lambda _event: self.next())
        self.show_step()

    def _clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def _title(self, text: str, detail: str = "") -> None:
        ttk.Label(self.content, text=text, font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(0, 10))
        if detail:
            ttk.Label(self.content, text=detail, wraplength=640).pack(anchor="w", pady=(0, 20))

    def show_step(self) -> None:
        self._clear()
        self.back_button.configure(state="disabled" if self.step == 0 else "normal")
        self.next_button.configure(text="Save Setup and Continue" if self.step == 4 else "Next")
        self.step_label.configure(text=f"Step {self.step + 1} of 5")
        (self._welcome, self._school, self._logo, self._year, self._complete)[self.step]()

    def _welcome(self) -> None:
        self._title("Welcome to SFMS", "For security, replace the default administrator password before continuing.")
        ttk.Label(self.content, text="New administrator password").pack(anchor="w")
        ttk.Entry(self.content, textvariable=self.password, show="*", width=45).pack(anchor="w", pady=(3, 12))
        ttk.Label(self.content, text="Confirm password").pack(anchor="w")
        ttk.Entry(self.content, textvariable=self.confirm_password, show="*", width=45).pack(anchor="w", pady=3)

    def _school(self) -> None:
        self._title("School Information", "This information appears on receipts and reports.")
        ttk.Label(self.content, text="School name *").pack(anchor="w")
        ttk.Entry(self.content, textvariable=self.school_name, width=65).pack(anchor="w", pady=(3, 12))
        ttk.Label(self.content, text="School address").pack(anchor="w")
        ttk.Entry(self.content, textvariable=self.school_address, width=65).pack(anchor="w", pady=3)

    def _logo(self) -> None:
        self._title("School Logo", "Optional. Select a local image or continue without one.")
        row = ttk.Frame(self.content)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.logo_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse", command=self.choose_logo).pack(side="left", padx=8)
        self.logo_preview = ttk.Label(self.content, text="No logo selected")
        self.logo_preview.pack(anchor="w", pady=20)
        self._show_logo()

    def choose_logo(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=(("Images", "*.png *.jpg *.jpeg *.bmp *.gif"),))
        if path:
            self.logo_path.set(path)
            self._show_logo()

    def _show_logo(self) -> None:
        if not hasattr(self, "logo_preview"):
            return
        path = Path(self.logo_path.get())
        if not path.is_file():
            self.logo_preview.configure(text="No logo selected", image="")
            return
        with Image.open(path) as source:
            image = source.copy()
        image.thumbnail((180, 120))
        self.logo_image = ImageTk.PhotoImage(image)
        self.logo_preview.configure(text="", image=self.logo_image)

    def _year(self) -> None:
        self._title("Academic Year", "Enter dates as DD-MM-YYYY.")
        for title, variable in (("Label (for example 2026-27)", self.year_label), ("Start date", self.start_date), ("End date", self.end_date)):
            ttk.Label(self.content, text=title).pack(anchor="w")
            DateEntry(self.content, textvariable=variable, width=24).pack(anchor="w", pady=(3, 10))

    def _complete(self) -> None:
        self._title("Setup Ready", "Review the information below. Setup cannot be skipped.")
        summary = (
            f"School: {self.school_name.get()}\nAddress: {self.school_address.get() or 'Not entered'}\n"
            f"Logo: {self.logo_path.get() or 'Not selected'}\nAcademic year: {self.year_label.get()}\n"
            f"Dates: {self.start_date.get()} to {self.end_date.get()}"
        )
        ttk.Label(self.content, text=summary, justify="left", font=("Segoe UI", 11)).pack(anchor="w", pady=10)

    def _valid_step(self) -> bool:
        if self.step == 0:
            if len(self.password.get()) < 8 or self.password.get() != self.confirm_password.get():
                messagebox.showerror("Setup", "Administrator passwords must match and contain at least 8 characters.", parent=self)
                return False
        elif self.step == 1 and not self.school_name.get().strip():
            messagebox.showerror("Setup", "School name is required.", parent=self)
            return False
        elif self.step == 3:
            if not self.year_label.get().strip():
                messagebox.showerror("Setup", "Academic year label is required.", parent=self)
                return False
            try:
                start = datetime.strptime(self.start_date.get(), "%d-%m-%Y")
                end = datetime.strptime(self.end_date.get(), "%d-%m-%Y")
            except ValueError:
                messagebox.showerror("Setup", "Enter valid dates in DD-MM-YYYY format.", parent=self)
                return False
            if end <= start:
                messagebox.showerror("Setup", "Academic year end date must follow the start date.", parent=self)
                return False
        return True

    def next(self) -> None:
        if self.step == 4:
            self.finish()
            return
        if self._valid_step():
            self.step += 1
            self.show_step()

    def back(self) -> None:
        if self.step:
            self.step -= 1
            self.show_step()

    def finish(self) -> None:
        """Persist setup atomically and keep the wizard open if saving fails."""
        self.next_button.configure(state="disabled")
        try:
            password_hash = bcrypt.hashpw(self.password.get().encode(), bcrypt.gensalt()).decode()
            with _connect() as conn:
                admin = conn.execute(
                    "SELECT id FROM users WHERE role='ADMIN' AND is_active=1 ORDER BY id LIMIT 1"
                ).fetchone()
                if admin is None:
                    raise RuntimeError("No active designated administrator account exists.")
                admin_id = int(admin[0])
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, admin_id))
                settings = {
                    "school_name": self.school_name.get().strip(), "school_address": self.school_address.get().strip(),
                    "logo_path": self.logo_path.get().strip(), "setup_complete": "1", "ui_theme": "light", "ui_language": "en",
                }
                for key, value in settings.items():
                    conn.execute(
                        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value),
                    )
                conn.execute("UPDATE academic_years SET is_active=0")
                conn.execute(
                    "INSERT INTO academic_years(label,start_date,end_date,is_active) VALUES (?,?,?,1) "
                    "ON CONFLICT(label) DO UPDATE SET start_date=excluded.start_date,end_date=excluded.end_date,is_active=1",
                    (self.year_label.get().strip(), self.start_date.get(), self.end_date.get()),
                )
                log_action(
                    conn, auth.CURRENT_SESSION.user_id, "setup_completed", "settings",
                    "setup_complete", "0", "1",
                )
        except Exception as exc:
            self.next_button.configure(state="normal")
            messagebox.showerror("Setup could not be saved", str(exc), parent=self)
            return
        self.grab_release()
        self.destroy()
        if self.on_complete:
            self.on_complete()
