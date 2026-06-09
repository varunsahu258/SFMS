"""Academic-year management screen for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ui_master_utils import audit, connect_db, ensure_admin_write


class AcademicYearWindow(tk.Toplevel):
    """Window for adding, activating, and safely deleting academic years."""

    def __init__(self, master=None):
        """Create the academic-year management window."""
        super().__init__(master)
        self.title("Academic Years")
        self.geometry("640x430")
        self.configure(bg=SPLASH_BG)
        self.label_var = tk.StringVar()
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self._build_widgets()
        self.refresh()

    def _build_widgets(self) -> None:
        """Build list and action controls."""
        self.tree = ttk.Treeview(self, columns=("id", "label", "start", "end", "active"), show="headings")
        for column, heading, width in (
            ("id", "ID", 60), ("label", "Label", 120), ("start", "Start Date", 130), ("end", "End Date", 130), ("active", "Active", 80),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=12)

        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="x", padx=12, pady=(0, 12))
        for label, var in (("Label", self.label_var), ("Start", self.start_var), ("End", self.end_var)):
            tk.Label(form, text=label, bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
            ttk.Entry(form, textvariable=var, width=12).pack(side="left", padx=5)
        ttk.Button(form, text="Add New", command=self.add_year).pack(side="left", padx=5)
        ttk.Button(form, text="Set Active", command=self.set_active).pack(side="left", padx=5)
        ttk.Button(form, text="Delete", command=self.delete_year).pack(side="left", padx=5)

    def refresh(self) -> None:
        """Reload academic years."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        with connect_db() as conn:
            rows = conn.execute("SELECT id, label, start_date, end_date, is_active FROM academic_years ORDER BY label").fetchall()
        for row in rows:
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["label"], row["start_date"], row["end_date"], "Yes" if row["is_active"] else "No"))

    def _selected_id(self) -> int | None:
        """Return selected academic-year id, if any."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Select year", "Please select an academic year.")
            return None
        return int(selected[0])

    @auth.require_role("ADMIN")
    def add_year(self) -> None:
        """Insert a new academic year."""
        if not ensure_admin_write():
            return
        label = self.label_var.get().strip()
        start = self.start_var.get().strip()
        end = self.end_var.get().strip()
        if not label or not start or not end:
            messagebox.showerror("Validation", "Label, start date, and end date are required.")
            return
        with connect_db() as conn:
            cursor = conn.execute(
                "INSERT INTO academic_years (label, start_date, end_date, is_active) VALUES (?, ?, ?, 0)",
                (label, start, end),
            )
            audit(conn, "ACADEMIC_YEAR_ADD", "academic_years", cursor.lastrowid, None, {"label": label, "start_date": start, "end_date": end})
        self.refresh()

    @auth.require_role("ADMIN")
    def set_active(self) -> None:
        """Set the selected academic year active and all others inactive."""
        year_id = self._selected_id()
        if year_id is None or not ensure_admin_write():
            return
        with connect_db() as conn:
            old_rows = [dict(row) for row in conn.execute("SELECT id, label, is_active FROM academic_years")]
            conn.execute("UPDATE academic_years SET is_active = 0")
            conn.execute("UPDATE academic_years SET is_active = 1 WHERE id = ?", (year_id,))
            audit(conn, "ACADEMIC_YEAR_SET_ACTIVE", "academic_years", year_id, old_rows, {"active_id": year_id})
        self.refresh()

    @auth.require_role("ADMIN")
    def delete_year(self) -> None:
        """Delete a year only when no payment rows use it."""
        year_id = self._selected_id()
        if year_id is None or not ensure_admin_write():
            return
        with connect_db() as conn:
            year = conn.execute("SELECT label FROM academic_years WHERE id = ?", (year_id,)).fetchone()
            if year is None:
                return
            payment_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM payments p
                JOIN fee_structure fs ON fs.fee_head_id = p.fee_head_id
                WHERE fs.academic_year = ?
                """,
                (year["label"],),
            ).fetchone()[0]
            if payment_count:
                messagebox.showerror("Cannot delete", "This academic year has payments and cannot be deleted.")
                return
            old = dict(conn.execute("SELECT * FROM academic_years WHERE id = ?", (year_id,)).fetchone())
            conn.execute("DELETE FROM academic_years WHERE id = ?", (year_id,))
            audit(conn, "ACADEMIC_YEAR_DELETE", "academic_years", year_id, old, None)
        self.refresh()
