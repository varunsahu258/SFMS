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
        self.title("View Student Details")
        self.geometry("1180x720")
        self.search_var = tk.StringVar()
        self.class_var = tk.StringVar(value="All Classes")
        self.gender_var = tk.StringVar(value="All Genders")
        self.rte_var = tk.StringVar(value="All RTE")
        self.address_var = tk.StringVar()
        self._build_widgets()
        self.search()

    def _classes(self) -> list[str]:
        with connect_db() as conn:
            return ["All Classes", *[row[0] for row in conn.execute("SELECT name FROM classes WHERE is_active=1 ORDER BY name")]]

    def _build_widgets(self) -> None:
        page = ttk.Frame(self, padding=22)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="View Student Details", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Read-only profiles with filters for address, gender, class, and RTE status.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        top = ttk.Frame(page)
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search_var, width=26)
        entry.pack(side="left", padx=5)
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Label(top, text="Address").pack(side="left", padx=(8, 0))
        address = ttk.Entry(top, textvariable=self.address_var, width=18)
        address.pack(side="left", padx=5)
        address.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Label(top, text="Class").pack(side="left", padx=(8, 0))
        class_filter = ttk.Combobox(top, textvariable=self.class_var, values=self._classes(), state="readonly", width=14)
        class_filter.pack(side="left", padx=5)
        class_filter.bind("<<ComboboxSelected>>", lambda _event: self.search())
        ttk.Label(top, text="Gender").pack(side="left", padx=(8, 0))
        gender_filter = ttk.Combobox(top, textvariable=self.gender_var, values=("All Genders", "Male", "Female", "Other"), state="readonly", width=12)
        gender_filter.pack(side="left", padx=5)
        gender_filter.bind("<<ComboboxSelected>>", lambda _event: self.search())
        ttk.Label(top, text="RTE").pack(side="left", padx=(8, 0))
        rte_filter = ttk.Combobox(top, textvariable=self.rte_var, values=("All RTE", "RTE", "Non-RTE"), state="readonly", width=10)
        rte_filter.pack(side="left", padx=5)
        rte_filter.bind("<<ComboboxSelected>>", lambda _event: self.search())
        ttk.Button(top, text="Search", command=self.search, style="Accent.TButton").pack(side="left", padx=8)
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=12)
        self.tree = ttk.Treeview(body, columns=("scholar", "name", "class", "gender", "rte", "father", "phone"), show="headings")
        for col, title, width in (("scholar", "Scholar No.", 95), ("name", "Student", 180),
                                  ("class", "Class", 90), ("gender", "Gender", 75), ("rte", "RTE", 55),
                                  ("father", "Father", 155), ("phone", "Mobile", 105)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_profile)
        self.profile = tk.Text(body, width=58, wrap="word", state="disabled", padx=14, pady=12)
        self.profile.pack(side="left", fill="both", expand=True, padx=(12, 0))

    def search(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        address = f"%{self.address_var.get().strip()}%"
        where = ["(name LIKE ? OR scholar_no LIKE ? OR father_name LIKE ? OR aadhaar LIKE ? OR phone LIKE ?)", "address LIKE ?"]
        params: list[object] = [term, term, term, term, term, address]
        if self.class_var.get() != "All Classes":
            where.append("class=?")
            params.append(self.class_var.get())
        if self.gender_var.get() != "All Genders":
            where.append("gender=?")
            params.append(self.gender_var.get())
        if self.rte_var.get() != "All RTE":
            where.append("COALESCE(is_rte,0)=?")
            params.append(1 if self.rte_var.get() == "RTE" else 0)
        with connect_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM students WHERE {' AND '.join(where)} ORDER BY is_active DESC,class,name LIMIT 500",
                params,
            ).fetchall()
        self.rows = {str(row["id"]): dict(row) for row in rows}
        for key, row in self.rows.items():
            class_text = f"{row.get('class') or ''}{' / ' + row.get('section') if row.get('section') else ''}"
            self.tree.insert("", "end", iid=key, values=(row.get("scholar_no") or "", row.get("name") or "",
                class_text, row.get("gender") or "", "Yes" if row.get("is_rte") else "No",
                row.get("father_name") or "", row.get("phone") or ""))

    def show_profile(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        row = self.rows[selected[0]]
        fields = (("Scholar No.", "scholar_no"), ("Student Name", "name"), ("Father's Name", "father_name"),
                  ("Father's Education", "father_education"), ("Father's Occupation", "father_occupation"),
                  ("Mother's Name", "mother_name"), ("Mother's Education", "mother_education"),
                  ("Mother's Occupation", "mother_occupation"), ("Family Annual Income", "family_annual_income"),
                  ("Class", "class"), ("Section", "section"), ("RTE Student", "is_rte"),
                  ("Date of Birth", "dob"), ("Admission Date", "admission_date"), ("Gender", "gender"),
                  ("Category", "category"), ("Mobile 1", "phone"), ("Mobile 2", "mobile2"),
                  ("SSSM ID", "sssm_id"), ("Aadhaar", "aadhaar"), ("eKYC Status", "ekyc_status"),
                  ("Address", "address"), ("Conveyance Details", "conveyance_details"),
                  ("Bank Account Number", "bank_account_number"), ("IFSC Code", "ifsc_code"),
                  ("Status", "status"))
        lines = []
        for label, key in fields:
            value = row.get(key)
            if key == "is_rte":
                value = "Yes" if value else "No"
            lines.append(f"{label}:  {value or '-'}")
        self.profile.configure(state="normal")
        self.profile.delete("1.0", "end")
        self.profile.insert("1.0", "\n".join(lines))
        self.profile.configure(state="disabled")
