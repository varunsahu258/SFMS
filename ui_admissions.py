"""New-admission workflow with an admission-only one-time charge."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import STATUS_ACTIVE
from ledger import active_academic_year
from ui_date import DateEntry
from ui_master_utils import audit, connect_db, ensure_permission_write
from ui_students import StudentDialog
from ui_workspace import WorkspacePage
from utils import format_currency, now_str, today_str


def create_admission(conn, values: dict, admission_fee: Decimal, due_date: str,
                     register_type: str, user_id: int) -> tuple[int, int | None]:
    """Create a student and optional one-time admission charge atomically."""
    columns = tuple(values)
    student = conn.execute(
        f"INSERT INTO students ({','.join(columns)},guardian_name,is_active,status,created_at) "
        f"VALUES ({','.join('?' for _ in columns)},?,1,?,?)",
        (*values.values(), values["father_name"] or "", STATUS_ACTIVE, now_str()),
    )
    charge_id = None
    if admission_fee > 0:
        head_name = f"Admission Fee - {'Small' if register_type == 'SMALL' else 'Main'} Register"
        existing = conn.execute("SELECT id FROM fee_heads WHERE name=? ORDER BY id LIMIT 1", (head_name,)).fetchone()
        if existing is None:
            head_id = conn.execute(
                "INSERT INTO fee_heads(name,register_type,is_active,is_one_time) VALUES(?,?,1,1)",
                (head_name, register_type),
            ).lastrowid
        else:
            head_id = existing[0]
        charge = conn.execute(
            """INSERT INTO student_charges(student_id,academic_year,fee_structure_id,fee_head_id,
                   original_amount,due_date,status,created_at) VALUES(?,?,NULL,?,?,?,'OPEN',?)""",
            (student.lastrowid, active_academic_year(conn), head_id, str(admission_fee), due_date, now_str()),
        )
        charge_id = int(charge.lastrowid)
    admission = conn.execute(
        """INSERT INTO admissions(student_id,charge_id,admission_fee,register_type,admitted_at,admitted_by)
           VALUES(?,?,?,?,?,?)""",
        (student.lastrowid, charge_id, str(admission_fee), register_type, now_str(), user_id),
    )
    audit(conn, "NEW_ADMISSION", "admissions", admission.lastrowid, new={
        "student_id": student.lastrowid, "admission_fee": str(admission_fee),
        "register_type": register_type, "charge_id": charge_id,
    })
    return int(student.lastrowid), charge_id


class AdmissionDialog(StudentDialog):
    """Capture a full student profile plus admission-only fee information."""

    def __init__(self, master, on_saved=None):
        self.admission_fee = tk.StringVar(value="0")
        self.fee_due_date = tk.StringVar(value=today_str())
        self.register_type = tk.StringVar(value="Main Register")
        super().__init__(master, "New Admission", on_saved)
        self.geometry("800x760")
        fee = ttk.LabelFrame(self, text="One-time Admission Fee", padding=12)
        fee.pack(fill="x", padx=18, pady=(0, 12))
        ttk.Label(fee, text="Amount").pack(side="left")
        ttk.Entry(fee, textvariable=self.admission_fee, width=12).pack(side="left", padx=6)
        ttk.Label(fee, text="Due Date").pack(side="left", padx=(10, 0))
        DateEntry(fee, textvariable=self.fee_due_date, width=13).pack(side="left", padx=6)
        ttk.Label(fee, text="Register").pack(side="left", padx=(10, 0))
        ttk.Combobox(fee, textvariable=self.register_type, values=("Main Register", "Small Register"),
                     state="readonly", width=15).pack(side="left", padx=6)
        ttk.Label(fee, text="This fee is charged only to this new admission.", style="Muted.TLabel").pack(side="left", padx=10)

    @auth.require_permission("manage_admissions")
    def save(self) -> None:
        if not ensure_permission_write("manage_admissions"):
            return
        try:
            amount = Decimal(self.admission_fee.get().strip() or "0")
            if not amount.is_finite() or amount < 0: raise ValueError
        except (InvalidOperation, ValueError):
            messagebox.showerror("Admission", "Admission fee must be zero or a positive amount.", parent=self); return
        with connect_db() as conn, conn:
            if not self._validate(conn): return
            register = "SMALL" if self.register_type.get() == "Small Register" else "BIG"
            create_admission(conn, self.values(), amount, self.fee_due_date.get(), register,
                             auth.CURRENT_SESSION.user_id)
        if self.on_saved: self.on_saved()
        messagebox.showinfo("Admission", "New admission saved successfully.", parent=self)
        self.destroy()


class AdmissionsWindow(WorkspacePage):
    """List admissions and start the dedicated new-admission workflow."""

    @auth.require_permission("manage_admissions")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Admissions"); self.geometry("1050x620")
        page = ttk.Frame(self, padding=20); page.pack(fill="both", expand=True)
        header = ttk.Frame(page); header.pack(fill="x")
        ttk.Label(header, text="Admissions", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="New Admission", command=self.new_admission,
                   style="Accent.TButton").pack(side="right")
        ttk.Label(page, text="Admission fees created here are one-time charges and are not added to every student.",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 12))
        columns = ("date", "scholar", "student", "class", "fee", "register", "by")
        self.tree = ttk.Treeview(page, columns=columns, show="headings")
        for key, heading, width in (("date", "Admitted", 140), ("scholar", "Scholar No.", 100),
                                    ("student", "Student", 220), ("class", "Class", 100),
                                    ("fee", "Admission Fee", 120), ("register", "Register", 100),
                                    ("by", "Admitted By", 120)):
            self.tree.heading(key, text=heading); self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True); self.refresh()

    def new_admission(self) -> None:
        AdmissionDialog(self, self.refresh)

    def refresh(self) -> None:
        for item in self.tree.get_children(): self.tree.delete(item)
        with connect_db() as conn:
            rows = conn.execute("""SELECT a.*,s.scholar_no,s.name,s.class,u.username FROM admissions a
                JOIN students s ON s.id=a.student_id LEFT JOIN users u ON u.id=a.admitted_by ORDER BY a.id DESC""").fetchall()
        for row in rows:
            self.tree.insert("", "end", values=(row["admitted_at"], row["scholar_no"] or "", row["name"],
                row["class"] or "", format_currency(row["admission_fee"]),
                "Small" if row["register_type"] == "SMALL" else "Main", row["username"] or ""))
