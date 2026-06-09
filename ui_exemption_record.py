"""Exemption recording screen for SFMS."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG
from ui_collection_common import active_academic_year, connect_db, search_students
from utils import now_str


class ExemptionWindow(tk.Toplevel):
    """Admin-only window for recording fee-head exemptions."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the exemption window."""
        super().__init__(master)
        self.title("Exemptions")
        self.geometry("820x580")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self.year_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.selected_student_id: int | None = None
        self.fee_heads: list[tuple[int, str]] = []
        self._build_widgets()
        self._load_defaults()

    def _build_widgets(self) -> None:
        """Build student search, fee-head multi-select, and exemption fields."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Entry(top, textvariable=self.search_var, width=34).pack(side="left", padx=6)
        ttk.Button(top, text="Search", command=self.search).pack(side="left")
        self.student_tree = ttk.Treeview(self, columns=("id", "name", "class", "aadhaar"), show="headings", height=5)
        for column in ("id", "name", "class", "aadhaar"):
            self.student_tree.heading(column, text=column.title())
        self.student_tree.pack(fill="x", padx=12, pady=8)
        self.student_tree.bind("<<TreeviewSelect>>", self._select_student)

        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="both", expand=True, padx=12, pady=10)
        tk.Label(form, text="Academic Year", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=0, column=0, sticky="w", pady=5)
        self.year_combo = ttk.Combobox(form, textvariable=self.year_var, state="readonly", width=20)
        self.year_combo.grid(row=0, column=1, sticky="w", pady=5)
        tk.Label(form, text="Fee Heads", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=0, sticky="nw", pady=5)
        self.head_listbox = tk.Listbox(form, selectmode="multiple", height=10, exportselection=False)
        self.head_listbox.grid(row=1, column=1, sticky="nsew", pady=5)
        tk.Label(form, text="Reason", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.reason_var, width=46).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="Save Exemption", command=self.save).grid(row=3, column=0, columnspan=2, pady=16)
        form.columnconfigure(1, weight=1)

    def _load_defaults(self) -> None:
        """Load academic years and active fee heads."""
        with connect_db() as conn:
            years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label")]
            active = active_academic_year(conn)
            rows = conn.execute("SELECT id, name FROM fee_heads WHERE is_active = 1 ORDER BY name").fetchall()
        self.year_combo.configure(values=years)
        self.year_var.set(active or (years[-1] if years else ""))
        self.fee_heads = [(row["id"], row["name"]) for row in rows]
        self.head_listbox.delete(0, "end")
        for _head_id, name in self.fee_heads:
            self.head_listbox.insert("end", name)

    def search(self) -> None:
        """Search students for exemption assignment."""
        auth.touch_session()
        for item in self.student_tree.get_children():
            self.student_tree.delete(item)
        for row in search_students(self.search_var.get().strip()):
            self.student_tree.insert("", "end", iid=str(row["id"]), values=(row["id"], row["name"], row["class"], row["aadhaar"] or ""))

    def _select_student(self, _event) -> None:
        """Store selected student id."""
        auth.touch_session()
        selection = self.student_tree.selection()
        self.selected_student_id = int(selection[0]) if selection else None

    @auth.require_role("ADMIN")
    def save(self) -> None:
        """Insert an exemption record with fee_head_ids stored as a JSON array."""
        if self.selected_student_id is None:
            messagebox.showerror("Validation", "Select a student.")
            return
        selected_indices = self.head_listbox.curselection()
        fee_head_ids = [self.fee_heads[index][0] for index in selected_indices]
        if not fee_head_ids or not self.year_var.get() or not self.reason_var.get().strip():
            messagebox.showerror("Validation", "Academic year, fee heads, and reason are required.")
            return
        with connect_db() as conn:
            conn.execute(
                "INSERT INTO exemptions (student_id, academic_year, fee_head_ids, reason, approved_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.selected_student_id,
                    self.year_var.get(),
                    json.dumps(fee_head_ids),
                    self.reason_var.get().strip(),
                    auth.CURRENT_SESSION.user_id,
                    now_str(),
                ),
            )
        messagebox.showinfo("Exemption", "Exemption saved.")
