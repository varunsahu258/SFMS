"""Prior-year opening balance entry for migration from manual fee records."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from opening_balance_service import record_opening_balance, student_balance_summary
from ui_date import DateEntry
from ui_master_utils import connect_db, current_user_id, ensure_permission_write
from ui_workspace import WorkspacePage
from utils import format_currency, today_str


class OpeningBalanceWindow(WorkspacePage):
    """Search students and enter immutable dues carried forward from manual records."""

    @auth.require_permission("manage_opening_balances")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Prior-Year Opening Balances")
        self.geometry("1100x700")
        self.search_var = tk.StringVar()
        self.year_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.note_var = tk.StringVar()
        self.due_date_var = tk.StringVar(value=today_str())
        self.selected_student_id: int | None = None
        self.summary_var = tk.StringVar(value="Select a student to view current and previous dues.")
        self._build()
        self._load_years()
        self.search()

    def _build(self) -> None:
        page = ttk.Frame(self, padding=20)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="Prior-Year Opening Balances", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            page,
            text="Enter unpaid balances from manual records. Each entry becomes an audited, immutable prior-year ledger charge.",
            style="Muted.TLabel", wraplength=900,
        ).pack(anchor="w", pady=(2, 14))

        search = ttk.Frame(page)
        search.pack(fill="x")
        ttk.Label(search, text="Search student").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.search_var, width=42)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Button(search, text="Search", command=self.search, style="Accent.TButton").pack(side="left")

        self.students = ttk.Treeview(page, columns=("scholar", "name", "father", "class"), show="headings", height=6)
        for key, heading, width in (("scholar", "Scholar No.", 120), ("name", "Student", 250),
                                    ("father", "Father's Name", 250), ("class", "Class / Section", 150)):
            self.students.heading(key, text=heading)
            self.students.column(key, width=width, anchor="w")
        self.students.pack(fill="x", pady=(10, 12))
        self.students.bind("<<TreeviewSelect>>", self._student_selected)

        ttk.Label(page, textvariable=self.summary_var, style="Muted.TLabel", wraplength=980).pack(anchor="w", pady=(0, 12))
        form = ttk.LabelFrame(page, text="Old balance details", padding=16)
        form.pack(fill="x")
        ttk.Label(form, text="Academic Year").grid(row=0, column=0, sticky="w", pady=5)
        self.year_combo = ttk.Combobox(form, textvariable=self.year_var, width=24)
        self.year_combo.grid(row=0, column=1, sticky="w", padx=(8, 28), pady=5)
        ttk.Label(form, text="Outstanding Amount").grid(row=0, column=2, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.amount_var, width=22).grid(row=0, column=3, sticky="w", padx=8, pady=5)
        ttk.Label(form, text="Original Due Date").grid(row=1, column=0, sticky="w", pady=5)
        self.due_date = DateEntry(form, textvariable=self.due_date_var)
        self.due_date.grid(row=1, column=1, sticky="w", padx=(8, 28), pady=5)
        ttk.Label(form, text="Manual-record note").grid(row=1, column=2, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.note_var, width=38).grid(row=1, column=3, sticky="ew", padx=8, pady=5)
        form.columnconfigure(3, weight=1)
        self.save_button = ttk.Button(form, text="Save Opening Balance", command=self.save,
                                      style="Accent.TButton", state="disabled")
        self.save_button.grid(row=2, column=0, columnspan=4, sticky="e", pady=(14, 0))

        ttk.Label(page, text="Previously imported balances", font=(self.ui_font, 12, "bold")).pack(anchor="w", pady=(18, 6))
        self.entries = ttk.Treeview(page, columns=("year", "amount", "due", "note", "created"), show="headings", height=7)
        for key, heading, width in (("year", "Academic Year", 130), ("amount", "Original Balance", 140),
                                    ("due", "Due Date", 120), ("note", "Note", 350), ("created", "Entered On", 170)):
            self.entries.heading(key, text=heading)
            self.entries.column(key, width=width, anchor="w")
        self.entries.pack(fill="both", expand=True)

    def _load_years(self) -> None:
        with connect_db() as conn:
            active = conn.execute("SELECT label FROM academic_years WHERE is_active=1 LIMIT 1").fetchone()
            years = [str(row[0]) for row in conn.execute(
                "SELECT label FROM academic_years WHERE is_active=0 ORDER BY start_date DESC,label DESC"
            )]
        self.year_combo.configure(values=years)
        if years:
            self.year_var.set(years[0])
        elif active:
            self.year_var.set("")

    def search(self) -> None:
        for item in self.students.get_children():
            self.students.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        with connect_db() as conn:
            rows = conn.execute(
                """SELECT id,scholar_no,name,father_name,class,section FROM students
                   WHERE is_active=1 AND (name LIKE ? OR scholar_no LIKE ? OR father_name LIKE ?)
                   ORDER BY class,name LIMIT 300""", (term, term, term),
            ).fetchall()
        for row in rows:
            class_text = f"{row['class'] or ''}{' / ' + row['section'] if row['section'] else ''}"
            self.students.insert("", "end", iid=str(row["id"]), values=(
                row["scholar_no"] or "", row["name"], row["father_name"] or "", class_text,
            ))

    def _student_selected(self, _event=None) -> None:
        selected = self.students.selection()
        self.selected_student_id = int(selected[0]) if selected else None
        self.save_button.configure(state="normal" if selected else "disabled")
        self._refresh_student()

    def _refresh_student(self) -> None:
        for item in self.entries.get_children():
            self.entries.delete(item)
        if self.selected_student_id is None:
            return
        with connect_db() as conn:
            summary = student_balance_summary(conn, self.selected_student_id)
            rows = conn.execute(
                """SELECT academic_year,amount,due_date,note,created_at FROM opening_balances
                   WHERE student_id=? ORDER BY academic_year,created_at""", (self.selected_student_id,),
            ).fetchall()
        self.summary_var.set(
            f"Previous-year outstanding: {format_currency(summary['previous_due'])}   |   "
            f"Current year ({summary['current_year'] or 'not selected'}): {format_currency(summary['current_due'])}   |   "
            f"Total outstanding: {format_currency(summary['total_due'])}"
        )
        for row in rows:
            self.entries.insert("", "end", values=(row["academic_year"], format_currency(row["amount"]),
                                                    row["due_date"] or "", row["note"] or "", row["created_at"]))

    def save(self) -> None:
        if not ensure_permission_write("manage_opening_balances") or self.selected_student_id is None:
            return
        try:
            amount = Decimal(self.amount_var.get().strip())
        except InvalidOperation:
            messagebox.showerror("Opening Balance", "Enter a valid outstanding amount.", parent=self)
            return
        year = self.year_var.get().strip()
        due_date = self.due_date_var.get().strip()
        student_name = self.students.item(str(self.selected_student_id), "values")[1]
        if not messagebox.askyesno(
            "Confirm Opening Balance",
            f"Add {format_currency(amount)} as an old balance for {student_name} in {year}?\n\nThis financial entry cannot be edited or deleted.",
            parent=self,
        ):
            return
        try:
            with connect_db() as conn:
                record_opening_balance(conn, self.selected_student_id, year, amount, due_date,
                                       self.note_var.get(), int(current_user_id() or 0))
                conn.commit()
        except Exception as exc:
            messagebox.showerror("Opening Balance", str(exc), parent=self)
            return
        self.amount_var.set("")
        self.note_var.set("")
        self._refresh_student()
        messagebox.showinfo("Opening Balance", "The prior-year balance was added to the student ledger.", parent=self)
