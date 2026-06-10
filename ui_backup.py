"""Complete administrator backup and database management window for SFMS."""

from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import bcrypt

import auth
import backup
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from gdrive import DRIVE_SCOPES, upload_to_drive

SCHEDULE_HOURS = (2, 4, 6, 12, 24)


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for backup administration."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or update one settings value."""
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _format_size(size: int) -> str:
    """Format bytes as megabytes for database tool feedback."""
    return f"{size / (1024 * 1024):,.2f} MB"


class BackupWindow(tk.Toplevel):
    """Manage local, encrypted, scheduled, Drive, restore, and archive backups."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create all administrator backup-management sections."""
        super().__init__(master)
        self.title("Backup and Database Management")
        self.geometry("980x700")
        self.configure(bg=SPLASH_BG)
        self.selected_restore_file = tk.StringVar()
        self.encryption_var = tk.BooleanVar(value=False)
        self.schedule_var = tk.StringVar(value="6")
        self.archive_year_var = tk.StringVar()
        self.last_backup_var = tk.StringVar(value="Last backup: Never")
        self.preview_var = tk.StringVar(value="Select a backup file to preview.")
        self._load_settings()
        self._build_widgets()
        self.refresh_history()

    def _load_settings(self) -> None:
        """Load encryption, schedule, and academic-year options."""
        with _connect() as conn:
            settings = {row["key"]: str(row["value"] or "") for row in conn.execute("SELECT key, value FROM settings")}
            self.encryption_var.set(settings.get("backup_encryption_enabled", "0") == "1")
            self.schedule_var.set(settings.get("backup_interval_hours", "6"))
            self.years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label DESC")]
        if self.years:
            self.archive_year_var.set(self.years[0])

    def _build_widgets(self) -> None:
        """Build six management sections in a tabbed interface."""
        tk.Label(self, text="Backup and Database Management", bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 20, "bold")).pack(pady=(14, 8))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._build_backup_tab(self._tab(notebook, "Backup Now"))
        self._build_schedule_tab(self._tab(notebook, "Schedule"))
        self._build_history_tab(self._tab(notebook, "History"))
        self._build_restore_tab(self._tab(notebook, "Restore"))
        self._build_drive_tab(self._tab(notebook, "Google Drive"))
        self._build_tools_tab(self._tab(notebook, "Database Tools"))

    def _tab(self, notebook: ttk.Notebook, title: str) -> tk.Frame:
        frame = tk.Frame(notebook, bg=SPLASH_BG, padx=24, pady=24)
        notebook.add(frame, text=title)
        return frame

    def _build_backup_tab(self, frame: tk.Frame) -> None:
        tk.Label(frame, textvariable=self.last_backup_var, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 18))
        ttk.Button(frame, text="Backup Now", command=self.create_backup).pack(anchor="w", pady=6)
        ttk.Checkbutton(frame, text="Encrypt new backups", variable=self.encryption_var, command=self.save_encryption_setting).pack(anchor="w", pady=10)
        ttk.Button(frame, text="Set Master Password", command=self.set_master_password).pack(anchor="w", pady=6)
        tk.Label(
            frame,
            text="The master password is stored only as a bcrypt hash. Losing it makes encrypted backups unrecoverable.",
            bg=SPLASH_BG,
            fg=SPLASH_FG,
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=12)

    def _build_schedule_tab(self, frame: tk.Frame) -> None:
        tk.Label(frame, text="Automatic backup interval", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=0, column=0, sticky="w", padx=4, pady=8)
        ttk.Combobox(frame, textvariable=self.schedule_var, values=SCHEDULE_HOURS, state="readonly", width=12).grid(row=0, column=1, padx=8, pady=8)
        ttk.Label(frame, text="hours").grid(row=0, column=2, sticky="w")
        ttk.Button(frame, text="Save Schedule", command=self.save_schedule).grid(row=1, column=0, columnspan=3, pady=18)

    def _build_history_tab(self, frame: tk.Frame) -> None:
        columns = ("date", "file", "type", "created_by")
        self.history_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for column, heading, width in (
            ("date", "Date", 155), ("file", "File", 460),
            ("type", "Type", 90), ("created_by", "Created By", 130),
        ):
            self.history_tree.heading(column, text=heading)
            self.history_tree.column(column, width=width)
        self.history_tree.pack(fill="both", expand=True)
        ttk.Button(frame, text="Refresh", command=self.refresh_history).pack(pady=10)

    def _build_restore_tab(self, frame: tk.Frame) -> None:
        row = tk.Frame(frame, bg=SPLASH_BG)
        row.pack(fill="x", pady=8)
        ttk.Entry(row, textvariable=self.selected_restore_file).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse", command=self.browse_restore).pack(side="left", padx=8)
        buttons = tk.Frame(frame, bg=SPLASH_BG)
        buttons.pack(anchor="w", pady=8)
        ttk.Button(buttons, text="Preview", command=self.preview_selected).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Restore", command=self.restore_selected).pack(side="left")
        tk.Label(frame, textvariable=self.preview_var, bg=SPLASH_BG, fg=SPLASH_FG, justify="left", anchor="nw", wraplength=800).pack(fill="both", expand=True, pady=14)

    def _build_drive_tab(self, frame: tk.Frame) -> None:
        tk.Label(
            frame,
            text="Connect using a Google OAuth desktop-client secrets JSON file, then upload the latest local backup.",
            bg=SPLASH_BG,
            fg=SPLASH_FG,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))
        ttk.Button(frame, text="Connect Google Drive", command=self.connect_drive).pack(anchor="w", pady=6)
        ttk.Button(frame, text="Upload Latest Backup", command=self.upload_latest).pack(anchor="w", pady=6)

    def _build_tools_tab(self, frame: tk.Frame) -> None:
        ttk.Button(frame, text="Compact DB", command=self.compact_database).grid(row=0, column=0, sticky="w", pady=8)
        tk.Label(frame, text="Archive Year", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=0, sticky="w", pady=12)
        ttk.Combobox(frame, textvariable=self.archive_year_var, values=self.years, state="readonly", width=20).grid(row=1, column=1, padx=8)
        ttk.Button(frame, text="Archive Year", command=self.archive_selected_year).grid(row=1, column=2, padx=8)

    def _master_hash(self) -> str:
        with _connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'master_backup_password_hash'").fetchone()
        return str(row[0] or "") if row else ""

    def _password_material(self) -> str | None:
        """Prompt for the master password, verify bcrypt, and return stored hash material."""
        stored_hash = self._master_hash()
        if not stored_hash:
            messagebox.showerror("Backup Password", "Set the master backup password first.", parent=self)
            return None
        password = simpledialog.askstring("Master Backup Password", "Enter master password:", show="*", parent=self)
        if password is None:
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            messagebox.showerror("Backup Password", "Wrong password.", parent=self)
            return None
        return stored_hash

    def create_backup(self) -> None:
        """Create a manual plain or encrypted backup and refresh history."""
        auth.touch_session()
        try:
            with _connect() as conn:
                path = backup.manual_backup(conn, auth.CURRENT_SESSION.user_id)
        except Exception as exc:
            messagebox.showerror("Backup", str(exc), parent=self)
            return
        self.refresh_history()
        messagebox.showinfo("Backup", f"Backup created:\n{path}", parent=self)

    def save_encryption_setting(self) -> None:
        """Persist the optional encryption toggle, requiring a configured password."""
        if self.encryption_var.get() and not self._master_hash():
            self.encryption_var.set(False)
            messagebox.showerror("Backup Encryption", "Set the master password before enabling encryption.", parent=self)
            return
        with _connect() as conn:
            _set_setting(conn, "backup_encryption_enabled", "1" if self.encryption_var.get() else "0")

    def set_master_password(self) -> None:
        """Hash and store a confirmed master backup password."""
        password = simpledialog.askstring("Master Backup Password", "New master password:", show="*", parent=self)
        if password is None:
            return
        if len(password) < 8:
            messagebox.showerror("Backup Password", "Password must be at least 8 characters.", parent=self)
            return
        confirm = simpledialog.askstring("Master Backup Password", "Confirm master password:", show="*", parent=self)
        if password != confirm:
            messagebox.showerror("Backup Password", "Passwords do not match.", parent=self)
            return
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        with _connect() as conn:
            _set_setting(conn, "master_backup_password_hash", password_hash)
        messagebox.showinfo("Backup Password", "Master backup password saved.", parent=self)

    def save_schedule(self) -> None:
        with _connect() as conn:
            _set_setting(conn, "backup_interval_hours", self.schedule_var.get())
        messagebox.showinfo("Backup Schedule", f"Backup interval set to {self.schedule_var.get()} hours.", parent=self)

    def refresh_history(self) -> None:
        """Reload the latest 30 local/Drive backup log entries."""
        if hasattr(self, "history_tree"):
            for item in self.history_tree.get_children():
                self.history_tree.delete(item)
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id, filename, created_at, created_by, type FROM backups_log ORDER BY id DESC LIMIT 30"
            ).fetchall()
        if rows:
            self.last_backup_var.set(f"Last backup: {rows[0]['created_at']}")
        else:
            self.last_backup_var.set("Last backup: Never")
        if hasattr(self, "history_tree"):
            for row in rows:
                self.history_tree.insert("", "end", values=(row["created_at"], row["filename"], row["type"], row["created_by"]))

    def browse_restore(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select SFMS Backup",
            filetypes=(("SFMS backups", "*.db *.enc"), ("SQLite database", "*.db"), ("Encrypted backup", "*.enc")),
        )
        if path:
            self.selected_restore_file.set(path)
            self.preview_var.set("Click Preview to inspect the selected backup.")

    def _preview_database_path(self) -> tuple[str | None, str | None]:
        selected = self.selected_restore_file.get().strip()
        if not selected:
            messagebox.showerror("Restore", "Select a backup file.", parent=self)
            return None, None
        if selected.lower().endswith(".enc"):
            password_material = self._password_material()
            if password_material is None:
                return None, None
            temporary = backup.decrypt_backup(selected, password_material)
            return temporary, password_material
        return selected, None

    def preview_selected(self) -> None:
        database_path = None
        temporary = False
        try:
            database_path, _password = self._preview_database_path()
            if database_path is None:
                return
            temporary = database_path != self.selected_restore_file.get().strip()
            preview = backup.preview_backup(database_path)
        except Exception as exc:
            messagebox.showerror("Backup Preview", str(exc), parent=self)
            return
        finally:
            if temporary and database_path:
                Path(database_path).unlink(missing_ok=True)
        self.preview_var.set(
            f"Backup date: {preview['backup_date']}\n"
            f"Students: {preview['students']}\nPayments: {preview['payments']}\n"
            f"Receipts: {preview['receipts']}\nAcademic years: {', '.join(preview['academic_years']) or 'None'}"
        )

    def restore_selected(self) -> None:
        selected = self.selected_restore_file.get().strip()
        if not selected:
            messagebox.showerror("Restore", "Select a backup file.", parent=self)
            return
        password_material = self._password_material() if selected.lower().endswith(".enc") else None
        if selected.lower().endswith(".enc") and password_material is None:
            return
        try:
            backup.restore_backup(selected, password_material)
        except Exception as exc:
            messagebox.showerror("Restore", str(exc), parent=self)

    def connect_drive(self) -> None:
        """Run the desktop OAuth flow and store authorized-user token JSON."""
        client_secrets = filedialog.askopenfilename(
            parent=self,
            title="Select Google OAuth Client Secrets",
            filetypes=(("JSON files", "*.json"),),
        )
        if not client_secrets:
            return
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(client_secrets, DRIVE_SCOPES)
            credentials = flow.run_local_server(port=0)
            token_json = credentials.to_json()
            json.loads(token_json)
            with _connect() as conn:
                _set_setting(conn, "gdrive_token_json", token_json)
        except Exception as exc:
            messagebox.showerror("Google Drive", str(exc), parent=self)
            return
        messagebox.showinfo("Google Drive", "Google Drive connected.", parent=self)

    def upload_latest(self) -> None:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT filename FROM backups_log WHERE type IN ('MANUAL','AUTO') ORDER BY id DESC"
            ).fetchall()
        latest = next((row[0] for row in rows if Path(row[0]).is_file()), None)
        if latest is None:
            messagebox.showerror("Google Drive", "No local backup is available to upload.", parent=self)
            return
        try:
            file_id = upload_to_drive(latest)
        except Exception as exc:
            messagebox.showerror("Google Drive", str(exc), parent=self)
            return
        if file_id is None:
            messagebox.showerror("Google Drive", "Connect Google Drive first.", parent=self)
            return
        self.refresh_history()
        messagebox.showinfo("Google Drive", f"Upload completed. File ID: {file_id}", parent=self)

    def compact_database(self) -> None:
        try:
            with _connect() as conn:
                before, after = backup.compact_db(conn)
        except Exception as exc:
            messagebox.showerror("Compact DB", str(exc), parent=self)
            return
        messagebox.showinfo("Compact DB", f"Before: {_format_size(before)} | After: {_format_size(after)}", parent=self)

    def archive_selected_year(self) -> None:
        try:
            with _connect() as conn:
                path = backup.archive_year(conn, self.archive_year_var.get())
        except Exception as exc:
            messagebox.showerror("Archive Year", str(exc), parent=self)
            return
        messagebox.showinfo("Archive Year", f"Archive created without changing the live database:\n{path}", parent=self)
