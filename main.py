"""Application startup for SFMS."""

from __future__ import annotations

import queue
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
from app_events import BACKUP_WARNING, SESSION_TIMEOUT, ui_event_queue
from integrity import MachineAuthorizationRequired, record_machine_fingerprint, startup_integrity_check

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
            except Exception as exc:
                from backup import record_auto_backup_failure

                with sqlite3.connect(DB_PATH) as failure_conn:
                    _apply_pragmas(failure_conn)
                    record_auto_backup_failure(failure_conn, exc)

    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=auto_backup_loop, daemon=True).start()



def destroy_authenticated_windows(root: tk.Misc | None = None) -> None:
    """Destroy all Tk windows that may belong to an authenticated session."""
    root = root or tk._default_root
    candidates = []
    if root is not None:
        candidates.append(root)
        try:
            candidates.extend(root.winfo_children())
        except tk.TclError:
            pass
    for window in list(candidates):
        try:
            if isinstance(window, tk.Toplevel) and window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass


def handle_session_timeout(root: tk.Misc | None = None) -> None:
    """Handle a queued timeout event on Tk's main thread."""
    import auth

    auth.logout()
    destroy_authenticated_windows(root)
    messagebox.showinfo("Session timed out", "Your session has expired. Please log in again.")
    _launch_login_window()


def poll_ui_events(root: tk.Misc | None = None) -> None:
    """Poll worker-produced events and perform all Tk work on the main thread."""
    root = root or tk._default_root
    while True:
        try:
            event = ui_event_queue.get_nowait()
        except queue.Empty:
            break
        if event.type == SESSION_TIMEOUT:
            handle_session_timeout(root)
        elif event.type == BACKUP_WARNING:
            _show_backup_failure_banner(root, int((event.payload or {}).get("failures", 0)))
    if root is not None:
        try:
            root.after(1000, lambda: poll_ui_events(root))
        except tk.TclError:
            pass


def _show_backup_failure_banner(root: tk.Misc | None, failures: int) -> None:
    """Display a non-dismissible backup warning on the main dashboard when present."""
    root = root or tk._default_root
    if root is None:
        return
    for child in [root, *getattr(root, "winfo_children", lambda: [])()]:
        if hasattr(child, "show_backup_failure_warning"):
            child.show_backup_failure_warning(failures)
            return
    messagebox.showwarning(
        "Automatic backup failed",
        f"Automatic backups have failed {failures} consecutive times. Open Backup Status and run Backup Now.",
        parent=root if hasattr(root, "winfo_exists") else None,
    )

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


def _launch_login_window() -> None:
    """Open the normal login window."""
    from ui_login import LoginWindow

    LoginWindow(on_dashboard_open=start_timeout_monitor)


def _open_login(root: tk.Tk) -> None:
    """Close the splash window and open the appropriate pre-authentication screen."""
    root.destroy()
    with sqlite3.connect(DB_PATH) as conn:
        from ui_first_time_setup import FirstTimeSetupWindow, first_time_setup_required

        if first_time_setup_required(conn):
            FirstTimeSetupWindow(on_complete=_launch_login_window)
            return
    _launch_login_window()


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

    poll_ui_events(root)
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
    except MachineAuthorizationRequired:
        integrity_conn.close()
        from ui_machine_authorization import MachineAuthorizationWindow

        MachineAuthorizationWindow(on_complete=show_splash)
        tk.mainloop()
        return
    except Exception:
        integrity_conn.close()
        raise
    show_splash()


if __name__ == "__main__":
    main()
