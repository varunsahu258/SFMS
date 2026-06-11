"""Read-only student search and profile viewer."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import auth
from ui_master_utils import connect_db
from ui_workspace import WorkspacePage


class StudentViewWindow(WorkspacePage):
    """Display complete student profiles without exposing edit controls."""

    @auth.require_permission("view_student_details")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("View Student Details"); self.geometry("1100x680")
        self.search_var = tk.StringVar(); self._build_widgets(); self.search()

    def _build_widgets(self) -> None:
        page = ttk.Frame(self, padding=22); page.pack(fill="both", expand=True)
        ttk.Label(page, text="View Student Details", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Read-only student profiles. No information can be changed here.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        top = ttk.Frame(page); top.pack(fill="x")
        entry = ttk.Entry(top, textvariable=self.search_var, width=42); entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Button(top, text="Search", command=self.search, style="Accent.TButton").pack(side="left", padx=8)
        body = ttk.Frame(page); body.pack(fill="both", expand=True, pady=12)
        self.tree = ttk.Treeview(body, columns=("scholar", "name", "class", "father", "phone"), show="headings", width=420)
        for col, title, width in (("scholar", "Scholar No.", 95), ("name", "Student", 180),
                                  ("class", "Class", 90), ("father", "Father", 170), ("phone", "Mobile", 105)):
            self.tree.heading(col, text=title); self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True); self.tree.bind("<<TreeviewSelect>>", self.show_profile)
        self.profile = tk.Text(body, width=55, wrap="word", state="disabled", padx=14, pady=12)
        self.profile.pack(side="left", fill="both", expand=True, padx=(12, 0))

    def search(self) -> None:
        for item in self.tree.get_children(): self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        with connect_db() as conn:
            rows = conn.execute("""SELECT * FROM students WHERE name LIKE ? OR scholar_no LIKE ? OR
                father_name LIKE ? OR aadhaar LIKE ? OR phone LIKE ? ORDER BY is_active DESC,class,name LIMIT 500""",
                (term, term, term, term, term)).fetchall()
        self.rows = {str(row["id"]): dict(row) for row in rows}
        for key, row in self.rows.items():
            class_text = f"{row.get('class') or ''}{' / ' + row.get('section') if row.get('section') else ''}"
            self.tree.insert("", "end", iid=key, values=(row.get("scholar_no") or "", row.get("name") or "",
                class_text, row.get("father_name") or "", row.get("phone") or ""))

    def show_profile(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected: return
        row = self.rows[selected[0]]
        fields = (("Scholar No.", "scholar_no"), ("Student Name", "name"), ("Father's Name", "father_name"),
                  ("Mother's Name", "mother_name"), ("Class", "class"), ("Section", "section"),
                  ("Date of Birth", "dob"), ("Admission Date", "admission_date"), ("Gender", "gender"),
                  ("Category", "category"), ("Mobile 1", "phone"), ("Mobile 2", "mobile2"),
                  ("SSSM ID", "sssm_id"), ("Aadhaar", "aadhaar"), ("eKYC Status", "ekyc_status"),
                  ("Address", "address"), ("Status", "status"))
        text = "\n".join(f"{label}:  {row.get(key) or '-'}" for label, key in fields)
        self.profile.configure(state="normal"); self.profile.delete("1.0", "end")
        self.profile.insert("1.0", text); self.profile.configure(state="disabled")
