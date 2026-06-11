"""Selective late-fee assessment for overdue students."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ledger import active_academic_year
from installment_service import overdue_installment_students
from ui_master_utils import audit, connect_db
from ui_workspace import WorkspacePage
from ui_date import DateEntry
from utils import now_str, today_str


def apply_late_fee_assessments(conn, student_ids: list[int], amount: Decimal, due_date: str,
                               reason: str, register: str, user_id: int,
                               installment_numbers: dict[int, int] | None = None) -> list[int]:
    """Create independently auditable late-fee charges for selected students."""
    year = active_academic_year(conn)
    head_name = f"Late Fee - {'Small' if register == 'SMALL' else 'Main'} Register"
    existing = conn.execute("SELECT id FROM fee_heads WHERE name=? ORDER BY id LIMIT 1", (head_name,)).fetchone()
    if existing is None:
        head_id = conn.execute(
            "INSERT INTO fee_heads(name,register_type,is_active) VALUES(?,?,1)", (head_name, register)
        ).lastrowid
    else:
        head_id = existing[0]
    assessment_ids = []
    columns = {row[1] for row in conn.execute("PRAGMA table_info(late_fee_assessments)")}
    for student_id in student_ids:
        installment_no = (installment_numbers or {}).get(student_id)
        has_installment_columns = {"academic_year", "installment_no", "register_type"} <= columns
        if installment_no is not None and has_installment_columns:
            duplicate = conn.execute(
                """SELECT 1 FROM late_fee_assessments WHERE student_id=? AND academic_year=?
                     AND installment_no=? AND register_type=?""",
                (student_id, year, installment_no, register),
            ).fetchone()
            if duplicate:
                continue
        charge = conn.execute(
            """INSERT INTO student_charges(student_id,academic_year,fee_structure_id,fee_head_id,
                   original_amount,due_date,status,created_at) VALUES(?,?,NULL,?,?,?,'OPEN',?)""",
            (student_id, year, head_id, str(amount), due_date, now_str()),
        )
        if installment_no is not None and has_installment_columns:
            assessment = conn.execute(
                """INSERT INTO late_fee_assessments(
                       student_id,charge_id,amount,due_date,reason,assessed_at,assessed_by,
                       academic_year,installment_no,register_type) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (student_id, charge.lastrowid, str(amount), due_date, reason, now_str(), user_id,
                 year, installment_no, register),
            )
        else:
            assessment = conn.execute(
                """INSERT INTO late_fee_assessments(student_id,charge_id,amount,due_date,reason,assessed_at,assessed_by)
                   VALUES(?,?,?,?,?,?,?)""",
                (student_id, charge.lastrowid, str(amount), due_date, reason, now_str(), user_id),
            )
        audit(conn, "LATE_FEE_APPLIED", "late_fee_assessments", assessment.lastrowid,
              new={"student_id": student_id, "amount": str(amount), "register": register, "reason": reason})
        assessment_ids.append(int(assessment.lastrowid))
    return assessment_ids


class LateFeeWindow(WorkspacePage):
    """Apply a separately tracked late-fee charge to selected students."""

    @auth.require_permission("apply_late_fees")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Apply Late Fees")
        self.geometry("1050x650")
        self.search_var = tk.StringVar()
        self.class_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.due_date_var = tk.StringVar(value=today_str())
        self.reason_var = tk.StringVar(value="Late fee")
        self.register_var = tk.StringVar(value="Main Register")
        self.overdue_only_var = tk.BooleanVar(value=True)
        self.as_of_var = tk.StringVar(value=today_str())
        self.overdue_rows: dict[int, dict] = {}
        self._build_widgets()
        self._load_classes()
        self.search()

    def _build_widgets(self) -> None:
        page = ttk.Frame(self, padding=22)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="Apply Late Fees", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Select only the students who should receive this separate charge.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        filters = ttk.Frame(page)
        filters.pack(fill="x")
        ttk.Label(filters, text="Search").pack(side="left")
        entry = ttk.Entry(filters, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=6)
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Label(filters, text="Class").pack(side="left", padx=(10, 0))
        self.class_combo = ttk.Combobox(filters, textvariable=self.class_var, state="readonly", width=16)
        self.class_combo.pack(side="left", padx=6)
        self.class_combo.bind("<<ComboboxSelected>>", lambda _event: self.search())
        ttk.Checkbutton(filters, text="Only overdue installments", variable=self.overdue_only_var,
                        command=self.search).pack(side="left", padx=(10, 4))
        ttk.Label(filters, text="Check as of").pack(side="left", padx=(4, 0))
        DateEntry(filters, textvariable=self.as_of_var, width=12).pack(side="left", padx=4)
        ttk.Button(filters, text="Refresh", command=self.search).pack(side="left", padx=4)
        ttk.Button(filters, text="Select Visible", command=self.select_visible).pack(side="right")

        self.tree = ttk.Treeview(page, columns=("scholar", "name", "father", "class", "phone", "installment"),
                                 show="headings", selectmode="extended", height=13)
        for column, heading, width in (("scholar", "Scholar No.", 110), ("name", "Student", 220),
                                       ("father", "Father's Name", 220), ("class", "Class", 120),
                                       ("phone", "Mobile", 120), ("installment", "Overdue installment", 180)):
            self.tree.heading(column, text=heading); self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=12)

        form = ttk.Frame(page)
        form.pack(fill="x")
        for label, variable, width in (("Amount", self.amount_var, 14), ("Due date", self.due_date_var, 16),
                                       ("Reason", self.reason_var, 30)):
            ttk.Label(form, text=label).pack(side="left", padx=(0, 4))
            widget = DateEntry(form, textvariable=variable, width=width) if label == "Due date" else ttk.Entry(form, textvariable=variable, width=width)
            widget.pack(side="left", padx=(0, 12))
        ttk.Combobox(form, textvariable=self.register_var, values=("Main Register", "Small Register"),
                     state="readonly", width=16).pack(side="left", padx=(0, 12))
        ttk.Button(form, text="Apply to Selected Students", command=self.apply,
                   style="Accent.TButton").pack(side="right")

    def _load_classes(self) -> None:
        with connect_db() as conn:
            values = [row[0] for row in conn.execute("SELECT DISTINCT class FROM students WHERE is_active=1 ORDER BY class") if row[0]]
        self.class_combo.configure(values=[""] + values)

    def search(self) -> None:
        auth.touch_session()
        for item in self.tree.get_children(): self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        class_name = self.class_var.get().strip()
        with connect_db() as conn:
            if self.overdue_only_var.get():
                try:
                    rows = overdue_installment_students(
                        conn, self.as_of_var.get(), class_name, self.search_var.get(),
                    )
                except ValueError:
                    rows = []
            else:
                rows = [dict(row) for row in conn.execute(
                    """SELECT id,scholar_no,name,father_name,class,section,phone FROM students
                       WHERE is_active=1 AND (name LIKE ? OR scholar_no LIKE ? OR father_name LIKE ?)
                         AND (?='' OR class=?) ORDER BY class,name""",
                    (term, term, term, class_name, class_name),
                ).fetchall()]
        self.overdue_rows = {int(row["id"]): dict(row) for row in rows}
        for row in rows:
            class_text = f"{row['class'] or ''}{' / ' + row['section'] if row.get('section') else ''}"
            status = ""
            if row.get("installments_due"):
                status = (f"#{row['installments_due']} • short {float(row['shortfall']):.2f} "
                          f"• due {row['last_due_date']}")
            self.tree.insert("", "end", iid=str(row["id"]),
                             values=(row.get("scholar_no") or "", row["name"], row.get("father_name") or "",
                                     class_text, row.get("phone") or "", status))

    def select_visible(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def apply(self) -> None:
        auth.touch_session()
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Late Fees", "Select at least one student.", parent=self); return
        try:
            amount = Decimal(self.amount_var.get().strip())
            if not amount.is_finite() or amount <= 0: raise ValueError
        except (InvalidOperation, ValueError):
            messagebox.showerror("Late Fees", "Enter a positive late-fee amount.", parent=self); return
        due_date = self.due_date_var.get().strip()
        try:
            datetime.strptime(due_date, "%d-%m-%Y")
        except ValueError:
            messagebox.showerror("Late Fees", "Due date must use DD-MM-YYYY format.", parent=self); return
        reason = self.reason_var.get().strip()
        if not reason:
            messagebox.showerror("Late Fees", "Enter a reason.", parent=self); return
        if not messagebox.askyesno("Confirm Late Fees", f"Apply {amount:.2f} to {len(selected)} selected student(s)?", parent=self):
            return
        register = "SMALL" if self.register_var.get() == "Small Register" else "BIG"
        with connect_db() as conn, conn:
            assessment_ids = apply_late_fee_assessments(
                conn, [int(value) for value in selected], amount,
                due_date, reason, register,
                auth.CURRENT_SESSION.user_id,
                {int(value): int(self.overdue_rows[int(value)]["installments_due"])
                 for value in selected if self.overdue_rows.get(int(value), {}).get("installments_due")},
            )
        skipped = len(selected) - len(assessment_ids)
        message = f"Late fee applied to {len(assessment_ids)} student(s)."
        if skipped:
            message += f" {skipped} already-assessed student(s) were skipped."
        messagebox.showinfo("Late Fees", message, parent=self)
