"""Administrator audit-log viewer and PDF export for SFMS."""

from __future__ import annotations

import os
import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from report_generator import audit_export

DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for audit operations."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_date(value: str) -> datetime | None:
    """Parse an optional audit filter date."""
    if not value.strip():
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format)
        except ValueError:
            continue
    raise ValueError("Dates must use DD-MM-YYYY format.")


class AuditLogWindow(WorkspacePage):
    """Filter, inspect, and export immutable audit-log records."""

    @auth.require_permission("view_audit_log")
    def __init__(self, master=None, *, embedded: bool = False):
        """Create the administrator-only audit viewer."""
        super().__init__(master, embedded=embedded)
        self.title("Audit Log")
        self.geometry("1220x650")
        self.configure(bg=SPLASH_BG)
        self.date_from_var = tk.StringVar()
        self.date_to_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.action_var = tk.StringVar()
        self.tamper_only_var = tk.BooleanVar(value=False)
        self.user_ids: dict[str, int] = {}
        self.rows: dict[str, dict] = {}
        self._build_widgets()
        self._load_filter_options()
        self.load_rows()

    def _build_widgets(self) -> None:
        """Build filters, audit tree, and export controls."""
        filters = tk.Frame(self, bg=SPLASH_BG)
        filters.pack(fill="x", padx=12, pady=10)
        for label, variable, width in (
            ("Date From", self.date_from_var, 13),
            ("Date To", self.date_to_var, 13),
        ):
            tk.Label(filters, text=label, bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(6, 2))
            ttk.Entry(filters, textvariable=variable, width=width).pack(side="left")
        tk.Label(filters, text="Username", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(10, 2))
        self.user_combo = ttk.Combobox(filters, textvariable=self.username_var, state="readonly", width=16)
        self.user_combo.pack(side="left")
        tk.Label(filters, text="Action Type", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(10, 2))
        self.action_combo = ttk.Combobox(filters, textvariable=self.action_var, state="readonly", width=22)
        self.action_combo.pack(side="left")
        ttk.Checkbutton(filters, text="Tamper Only", variable=self.tamper_only_var).pack(side="left", padx=10)
        ttk.Button(filters, text="Apply", command=self.load_rows).pack(side="left", padx=3)
        ttk.Button(filters, text="Clear", command=self.clear_filters).pack(side="left", padx=3)
        ttk.Button(filters, text="Export PDF", command=self.export_pdf).pack(side="right")

        columns = ("timestamp", "user", "action", "table", "record_id", "old", "new")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for column, heading, width in (
            ("timestamp", "Timestamp", 145), ("user", "User", 105),
            ("action", "Action", 155), ("table", "Table", 110),
            ("record_id", "Record ID", 120), ("old", "Old", 230), ("new", "New", 230),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=True)
        self.tree.tag_configure("tamper", foreground="red")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tree.bind("<Double-1>", self.show_detail)

    def _load_filter_options(self) -> None:
        """Load usernames and action values for filter dropdowns."""
        with _connect() as conn:
            self.user_ids = {row["username"]: row["id"] for row in conn.execute("SELECT id, username FROM users ORDER BY username")}
            actions = [row[0] for row in conn.execute("SELECT DISTINCT action FROM audit_log WHERE action IS NOT NULL ORDER BY action")]
        self.user_combo.configure(values=[""] + list(self.user_ids))
        self.action_combo.configure(values=[""] + actions)

    def _filters(self) -> dict:
        """Return validated filters shared by the viewer and PDF export."""
        _parse_date(self.date_from_var.get())
        _parse_date(self.date_to_var.get())
        filters = {
            "date_from": self.date_from_var.get().strip(),
            "date_to": self.date_to_var.get().strip(),
            "user_id": self.user_ids.get(self.username_var.get(), ""),
            "action": self.action_var.get().strip(),
        }
        if self.tamper_only_var.get():
            filters["tamper_attempt"] = 1
        return filters

    def load_rows(self) -> None:
        """Apply filters and populate the audit tree."""
        auth.touch_session()
        try:
            filters = self._filters()
        except ValueError as exc:
            messagebox.showerror("Audit filters", str(exc), parent=self)
            return
        clauses: list[str] = []
        params: list[object] = []
        for key, column in (("user_id", "a.user_id"), ("action", "a.action"), ("tamper_attempt", "a.tamper_attempt")):
            if filters.get(key) not in (None, ""):
                clauses.append(f"{column} = ?")
                params.append(filters[key])
        sql = """
            SELECT a.id, a.timestamp, COALESCE(u.username, '') AS username,
                   a.action, a.table_name, a.record_id, a.old_value,
                   a.new_value, a.tamper_attempt
            FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY a.id DESC"
        with _connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params)]
        date_from = _parse_date(filters["date_from"])
        date_to = _parse_date(filters["date_to"])
        if date_from or date_to:
            filtered = []
            for row in rows:
                try:
                    row_date = datetime.strptime(str(row["timestamp"]).split(" ")[0], "%d-%m-%Y")
                except (TypeError, ValueError):
                    continue
                if date_from and row_date < date_from:
                    continue
                if date_to and row_date > date_to:
                    continue
                filtered.append(row)
            rows = filtered

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        for row in rows:
            item_id = str(row["id"])
            self.rows[item_id] = row
            values = (
                row["timestamp"] or "", row["username"] or "", row["action"] or "",
                row["table_name"] or "", row["record_id"] or "",
                str(row["old_value"] or "")[:100], str(row["new_value"] or "")[:100],
            )
            tags = ("tamper",) if row["tamper_attempt"] else ()
            self.tree.insert("", "end", iid=item_id, values=values, tags=tags)

    def clear_filters(self) -> None:
        """Clear every audit filter and reload the full log."""
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.username_var.set("")
        self.action_var.set("")
        self.tamper_only_var.set(False)
        self.load_rows()

    def show_detail(self, _event=None) -> None:
        """Show the complete selected immutable audit record."""
        selected = self.tree.selection()
        if not selected:
            return
        row = self.rows[selected[0]]
        detail = tk.Toplevel(self)
        detail.title(f"Audit Record {row['id']}")
        detail.geometry("720x500")
        text = tk.Text(detail, wrap="word", padx=12, pady=12)
        text.pack(fill="both", expand=True)
        for label, key in (
            ("ID", "id"), ("Timestamp", "timestamp"), ("User", "username"),
            ("Action", "action"), ("Table", "table_name"), ("Record ID", "record_id"),
            ("Tamper Attempt", "tamper_attempt"), ("Old Value", "old_value"), ("New Value", "new_value"),
        ):
            text.insert("end", f"{label}:\n{row.get(key) or ''}\n\n")
        text.configure(state="disabled")

    def export_pdf(self) -> None:
        """Export the currently filtered audit log to a PDF."""
        auth.touch_session()
        try:
            filters = self._filters()
            with _connect() as conn:
                path = audit_export(conn, filters)
        except Exception as exc:
            messagebox.showerror("Audit export", str(exc), parent=self)
            return
        if hasattr(os, "startfile"):
            os.startfile(path)
        messagebox.showinfo("Audit export", f"PDF saved to:\n{path}", parent=self)
