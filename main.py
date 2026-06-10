"""Application startup for SFMS."""

from __future__ import annotations

import sqlite3
import threading
import time
import tkinter as tk
from tkinter import messagebox

from data_paths import assert_live_database_path, connection_database_path
from config import (
    APP_SUBTITLE,
    APP_TITLE,
    DB_PATH,
    SCHOOL_NAME,
    SETTING_SCHOOL_NAME,
    SPLASH_DURATION_MS,
)
from database import init_db
from integrity import record_machine_fingerprint, startup_integrity_check

_MONITORS_STARTED = False


def start_timeout_monitor() -> None:
    """Start the single timeout monitor and hourly automatic-backup worker."""
    global _MONITORS_STARTED
    if _MONITORS_STARTED:
        return
    _MONITORS_STARTED = True

    def monitor() -> None:
        """Check for session timeout every 60 seconds."""
        import auth

        while True:
            time.sleep(60)
            auth.check_timeout()

    def auto_backup_loop() -> None:
        """Check once per hour and create an automatic backup when overdue."""
        from backup import auto_backup

        while True:
            time.sleep(3600)
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    _apply_pragmas(conn)
                    auto_backup(conn)
            except Exception:
                continue

    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=auto_backup_loop, daemon=True).start()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply required SQLite pragmas for this database connection."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")


def _school_name() -> str:
    """Return the configured school name for the splash screen."""
    with sqlite3.connect(DB_PATH) as conn:
        _apply_pragmas(conn)
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING_SCHOOL_NAME,)).fetchone()
    return row[0] if row and row[0] else SCHOOL_NAME


def _open_login(root: tk.Tk) -> None:
    """Close the splash window and open the login window."""
    root.destroy()
    from ui_login import LoginWindow

    LoginWindow(on_dashboard_open=start_timeout_monitor)


def show_splash() -> None:
    """Show the two-second SFMS splash screen before login."""
    root = tk.Tk()
    from ui_theme import apply_theme

    apply_theme(root)
    root.title(APP_TITLE)
    root.configure(background=root._sfms_palette["bg"])
    root.overrideredirect(True)
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))

    width = 520
    height = 300
    x_position = (root.winfo_screenwidth() - width) // 2
    y_position = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x_position}+{y_position}")

    frame = tk.Frame(root, background=root._sfms_palette["bg"])
    frame.pack(expand=True, fill="both")

    tk.Label(
        frame,
        text=_school_name(),
        font=("Segoe UI", 18, "bold"),
        bg=root._sfms_palette["bg"],
        fg=root._sfms_palette["fg"],
        wraplength=460,
    ).pack(pady=(72, 12))
    tk.Label(
        frame,
        text=APP_TITLE,
        font=("Segoe UI", 44, "bold"),
        bg=root._sfms_palette["bg"],
        fg=root._sfms_palette["fg"],
    ).pack()
    tk.Label(
        frame,
        text=APP_SUBTITLE,
        font=("Segoe UI", 13),
        bg=root._sfms_palette["bg"],
        fg=root._sfms_palette["fg"],
    ).pack(pady=(8, 0))

    root.after(SPLASH_DURATION_MS, lambda: _open_login(root))
    root.mainloop()


def _exit_application(window) -> None:
    """Log out and destroy the Tk application root."""
    try:
        import auth

        auth.logout()
    except Exception:
        pass
    root = tk._default_root
    try:
        if window is not None and window.winfo_exists():
            window.destroy()
    except tk.TclError:
        pass
    try:
        if root is not None and root.winfo_exists():
            root.destroy()
    except tk.TclError:
        pass


def on_closing(window=None) -> None:
    """Check backup age in a worker before allowing the application to close."""
    target = window or tk._default_root
    if target is None or getattr(target, "_sfms_close_pending", False):
        return
    target._sfms_close_pending = True

    def worker() -> None:
        try:
            from notifications import backup_interval_hours, backup_overdue

            with sqlite3.connect(DB_PATH) as conn:
                _apply_pragmas(conn)
                overdue = backup_overdue(conn)
                interval = backup_interval_hours(conn)
        except sqlite3.Error:
            overdue = False
            interval = 0
        try:
            target.after(0, lambda: finish(overdue, interval))
        except tk.TclError:
            pass

    def finish(overdue: bool, interval: int) -> None:
        if not overdue:
            _exit_application(target)
            return
        take_backup = messagebox.askyesno(
            "Backup Reminder",
            f"No backup in {interval} hours. Take backup before closing?",
            parent=target,
        )
        if take_backup:
            from ui_backup import BackupWindow

            backup_window = BackupWindow(target)
            target.wait_window(backup_window)
        _exit_application(target)

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    """Initialize security controls and start the desktop application."""
    init_db()
    integrity_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    assert_live_database_path(connection_database_path(integrity_conn), DB_PATH)
    _apply_pragmas(integrity_conn)
    try:
        record_machine_fingerprint(integrity_conn)
        threading.Thread(
            target=startup_integrity_check,
            args=(integrity_conn,),
            daemon=True,
        ).start()
    except Exception:
        integrity_conn.close()
        raise
    show_splash()


if __name__ == "__main__":
    main()
