"""Administrator application settings and data tools for SFMS."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import bcrypt
from openpyxl import Workbook
from PIL import Image, ImageTk

import auth
import backup
from config import DB_PATH, REPORTS_DIR
from ui_theme import apply_theme

SCHEDULE_HOURS = (2, 4, 6, 12, 24)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _upsert(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def export_full_database_to_excel() -> str:
    """Export every non-SQLite table to a separate worksheet."""
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    path = Path(REPORTS_DIR) / f"sfms_full_export_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    workbook = Workbook()
    workbook.remove(workbook.active)
    with _connect() as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables:
            sheet = workbook.create_sheet(table[:31])
            cursor = conn.execute(f'SELECT * FROM "{table}"')
            headers = [item[0] for item in cursor.description]
            sheet.append(headers)
            for row in cursor:
                sheet.append(list(row))
            sheet.freeze_panes = "A2"
            for column in sheet.columns:
                width = min(max(len(str(cell.value or "")) for cell in column) + 2, 50)
                sheet.column_dimensions[column[0].column_letter].width = width
    workbook.save(path)
    return str(path)


class SettingsWindow(tk.Toplevel):
    """Edit school, appearance, security, backup, and data settings."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        super().__init__(master)
        self.title("SFMS Settings")
        self.geometry("820x650")
        self.transient(master)
        self.school_name = tk.StringVar()
        self.school_address = tk.StringVar()
        self.logo_path = tk.StringVar()
        self.academic_year = tk.StringVar()
        self.theme = tk.StringVar(value="light")
        self.language = tk.StringVar(value="en")
        self.timeout = tk.IntVar(value=15)
        self.backup_interval = tk.StringVar(value="6")
        self.encryption = tk.BooleanVar(value=False)
        self.archive_year = tk.StringVar()
        self.logo_image = None
        self._load()
        self._build()
        self._preview_logo()

    def _load(self) -> None:
        with _connect() as conn:
            values = {row["key"]: str(row["value"] or "") for row in conn.execute("SELECT key,value FROM settings")}
            self.years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label DESC")]
            active = conn.execute("SELECT label FROM academic_years WHERE is_active=1 LIMIT 1").fetchone()
        self.school_name.set(values.get("school_name", ""))
        self.school_address.set(values.get("school_address", ""))
        self.logo_path.set(values.get("logo_path", ""))
        self.theme.set(values.get("ui_theme", "light"))
        self.language.set(values.get("ui_language", "en"))
        self.timeout.set(int(values.get("session_timeout_minutes", "15") or 15))
        self.backup_interval.set(values.get("backup_interval_hours", "6"))
        self.encryption.set(values.get("backup_encryption_enabled", "0") == "1")
        self.academic_year.set(active[0] if active else (self.years[0] if self.years else ""))
        self.archive_year.set(self.academic_year.get())

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        general = ttk.Frame(notebook, padding=20)
        appearance = ttk.Frame(notebook, padding=20)
        security = ttk.Frame(notebook, padding=20)
        backup_tab = ttk.Frame(notebook, padding=20)
        data = ttk.Frame(notebook, padding=20)
        for frame, title in ((general, "General"), (appearance, "Appearance"), (security, "Security"), (backup_tab, "Backup"), (data, "Data")):
            notebook.add(frame, text=title)
        self._general(general)
        self._appearance(appearance)
        self._security(security)
        self._backup(backup_tab)
        self._data(data)
        ttk.Button(self, text="Save Settings", command=self.save).pack(pady=(0, 14))

    def _entry(self, parent, title, variable, row) -> None:
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable, width=52).grid(row=row, column=1, sticky="ew", pady=6, padx=8)
        parent.columnconfigure(1, weight=1)

    def _general(self, frame) -> None:
        self._entry(frame, "School name", self.school_name, 0)
        self._entry(frame, "School address", self.school_address, 1)
        self._entry(frame, "Logo path", self.logo_path, 2)
        ttk.Button(frame, text="Browse", command=self.choose_logo).grid(row=2, column=2)
        self.logo_preview = ttk.Label(frame, text="No logo")
        self.logo_preview.grid(row=3, column=1, sticky="w", pady=8)
        ttk.Label(frame, text="Active academic year").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.academic_year, values=self.years, state="readonly").grid(row=4, column=1, sticky="w", padx=8)

    def _appearance(self, frame) -> None:
        ttk.Label(frame, text="Theme", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Radiobutton(frame, text="Light", variable=self.theme, value="light", command=self._apply_appearance).pack(anchor="w", pady=5)
        ttk.Radiobutton(frame, text="Dark", variable=self.theme, value="dark", command=self._apply_appearance).pack(anchor="w", pady=5)
        ttk.Separator(frame).pack(fill="x", pady=14)
        ttk.Label(frame, text="Language", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Radiobutton(frame, text="English", variable=self.language, value="en").pack(anchor="w", pady=5)
        ttk.Radiobutton(frame, text="हिन्दी", variable=self.language, value="hi").pack(anchor="w", pady=5)

    def _security(self, frame) -> None:
        ttk.Label(frame, text="Session timeout (5–60 minutes)").pack(anchor="w")
        tk.Scale(frame, from_=5, to=60, orient="horizontal", variable=self.timeout, length=420).pack(anchor="w", pady=10)
        ttk.Button(frame, text="Reset Machine Fingerprint", command=self.reset_fingerprint).pack(anchor="w", pady=12)

    def _backup(self, frame) -> None:
        ttk.Label(frame, text="Backup interval (hours)").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Combobox(frame, textvariable=self.backup_interval, values=SCHEDULE_HOURS, state="readonly", width=10).grid(row=0, column=1, padx=8)
        ttk.Checkbutton(frame, text="Encrypt new backups", variable=self.encryption).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(frame, text="Set Master Backup Password", command=self.set_backup_password).grid(row=2, column=0, columnspan=2, sticky="w", pady=8)

    def _data(self, frame) -> None:
        ttk.Button(frame, text="Export Full DB to Excel", command=self.export_database).grid(row=0, column=0, sticky="ew", pady=6)
        ttk.Button(frame, text="Compact DB", command=self.compact).grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Combobox(frame, textvariable=self.archive_year, values=self.years, state="readonly").grid(row=2, column=0, sticky="ew", pady=6)
        ttk.Button(frame, text="Archive Year", command=self.archive).grid(row=2, column=1, padx=8)

    def choose_logo(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=(("Images", "*.png *.jpg *.jpeg *.bmp *.gif"),))
        if path:
            self.logo_path.set(path)
            self._preview_logo()

    def _preview_logo(self) -> None:
        path = Path(self.logo_path.get())
        if not path.is_file():
            if hasattr(self, "logo_preview"):
                self.logo_preview.configure(image="", text="No logo selected")
            return
        with Image.open(path) as source:
            image = source.copy()
        image.thumbnail((120, 80))
        self.logo_image = ImageTk.PhotoImage(image)
        self.logo_preview.configure(image=self.logo_image, text="")

    def _apply_appearance(self) -> None:
        apply_theme(self, self.theme.get(), self.language.get())

    def reset_fingerprint(self) -> None:
        if messagebox.askyesno("Machine Fingerprint", "Clear the stored machine fingerprint?", parent=self):
            with _connect() as conn:
                _upsert(conn, "machine_id", "")
            messagebox.showinfo("Machine Fingerprint", "Fingerprint cleared. It will be recorded at next startup.", parent=self)

    def set_backup_password(self) -> None:
        password = simpledialog.askstring("Backup Password", "New master password (minimum 8 characters):", show="*", parent=self)
        if not password or len(password) < 8:
            messagebox.showerror("Backup Password", "Password must be at least 8 characters.", parent=self)
            return
        confirm = simpledialog.askstring("Backup Password", "Confirm password:", show="*", parent=self)
        if password != confirm:
            messagebox.showerror("Backup Password", "Passwords do not match.", parent=self)
            return
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        with _connect() as conn:
            _upsert(conn, "master_backup_password_hash", hashed)
        messagebox.showinfo("Backup Password", "Master backup password saved.", parent=self)

    def export_database(self) -> None:
        auth.touch_session()
        try:
            path = export_full_database_to_excel()
        except Exception as exc:
            messagebox.showerror("Data Export", str(exc), parent=self)
            return
        messagebox.showinfo("Data Export", f"Export saved:\n{path}", parent=self)

    def compact(self) -> None:
        with _connect() as conn:
            before, after = backup.compact_db(conn)
        messagebox.showinfo("Compact DB", f"Before: {before / 1048576:,.2f} MB | After: {after / 1048576:,.2f} MB", parent=self)

    def archive(self) -> None:
        with _connect() as conn:
            path = backup.archive_year(conn, self.archive_year.get())
        messagebox.showinfo("Archive", f"Archive created without clearing live data:\n{path}", parent=self)

    def save(self) -> None:
        auth.touch_session()
        if not self.school_name.get().strip():
            messagebox.showerror("Settings", "School name is required.", parent=self)
            return
        if self.encryption.get():
            with _connect() as conn:
                has_password = conn.execute("SELECT 1 FROM settings WHERE key='master_backup_password_hash' AND value<>''").fetchone()
            if not has_password:
                messagebox.showerror("Settings", "Set the master backup password before enabling encryption.", parent=self)
                return
        values = {
            "school_name": self.school_name.get().strip(), "school_address": self.school_address.get().strip(),
            "logo_path": self.logo_path.get().strip(), "ui_theme": self.theme.get(), "ui_language": self.language.get(),
            "session_timeout_minutes": str(self.timeout.get()), "backup_interval_hours": self.backup_interval.get(),
            "backup_encryption_enabled": "1" if self.encryption.get() else "0",
        }
        with _connect() as conn:
            for key, value in values.items():
                _upsert(conn, key, value)
            if self.academic_year.get():
                conn.execute("UPDATE academic_years SET is_active=CASE WHEN label=? THEN 1 ELSE 0 END", (self.academic_year.get(),))
        language, _font = apply_theme(self, self.theme.get(), self.language.get())
        if self.language.get() == "hi" and language != "hi":
            self.language.set("en")
            with _connect() as conn:
                _upsert(conn, "ui_language", "en")
        messagebox.showinfo("Settings", "Settings saved and theme applied. Restart SFMS to refresh every open window.", parent=self)
