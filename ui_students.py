"""Student master-data management screens for SFMS."""

from __future__ import annotations

import re
import sqlite3
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import auth
from config import SPLASH_BG, SPLASH_FG, STATUS_ACTIVE
from ui_master_utils import audit, connect_db, ensure_admin_write
from utils import now_str

CLASS_MAP = {
    "NUR": "Nursery",
    "NURSARY": "Nursery",
    "NURSERY": "Nursery",
    "KG 1": "KG-I",
    "KG IST": "KG-I",
    "KG I": "KG-I",
    "KG 2": "KG-II",
    "KG IIND": "KG-II",
    "KG II": "KG-II",
    "I": "Class 1",
    "IST": "Class 1",
    "1ST": "Class 1",
    "II": "Class 2",
    "III": "Class 3",
    "IV": "Class 4",
    "V": "Class 5",
    "VI": "Class 6",
    "VII": "Class 7",
    "VIII": "Class 8",
    "IX": "Class 9",
    "X": "Class 10",
}
PROMOTION_ORDER = [
    "Nursery", "KG-I", "KG-II", "Class 1", "Class 2", "Class 3", "Class 4",
    "Class 5", "Class 6", "Class 7", "Class 8", "Class 9", "Class 10",
]
FEE_HEADS = (
    ("Admission Fee", "BIG"),
    ("Tuition Fee", "BOTH"),
    ("Term Exam Fee", "BIG"),
    ("Computer Fee", "BIG"),
    ("Sports & Activity Fee", "BIG"),
    ("Vehicle Fee", "SMALL"),
)
FEE_SEED = {
    "Nursery": (1000, 7600, 400, 0, 300),
    "KG-I": (1000, 7800, 400, 0, 300),
    "KG-II": (1000, 7800, 400, 0, 300),
    "Class 1": (2000, 8800, 500, 300, 400),
    "Class 2": (2000, 8800, 500, 300, 400),
    "Class 3": (2000, 9000, 500, 300, 400),
    "Class 4": (2000, 9000, 500, 300, 400),
    "Class 5": (2000, 9000, 500, 300, 400),
    "Class 6": (2000, 9900, 500, 300, 500),
    "Class 7": (2000, 9900, 500, 300, 500),
    "Class 8": (2000, 9900, 500, 300, 500),
    "Class 9": (3000, 11400, 600, 300, 500),
    "Class 10": (3000, 11400, 600, 300, 500),
}
VEHICLE_FEES = {"BARELI": 3000, "KAMTONE": 3600, "PIPARIYA": 4200}
AADHAAR_RE = re.compile(r"^\d{12}$")
PHONE_RE = re.compile(r"^\d{10}$")


class StudentWindow(tk.Toplevel):
    """Admin-only student management window."""

    @auth.require_role("ADMIN")
    def __init__(self, master=None):
        """Create the student management window."""
        super().__init__(master)
        self.title("Students")
        self.geometry("920x560")
        self.configure(bg=SPLASH_BG)
        self.search_var = tk.StringVar()
        self._ensure_import_columns()
        self._build_widgets()
        self.refresh()

    def _ensure_import_columns(self) -> None:
        """Add optional school-import columns if this database does not have them yet."""
        with connect_db() as conn:
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(students)")}
            for column, ddl in {
                "dob": "TEXT",
                "gender": "TEXT",
                "category": "TEXT",
                "route": "TEXT",
                "vehicle_fee": "REAL DEFAULT 0",
                "has_vehicle_fee": "INTEGER DEFAULT 0",
            }.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE students ADD COLUMN {column} {ddl}")

    def _build_widgets(self) -> None:
        """Build search, tree, and action controls."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Search", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search_var, width=42)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _event: self.refresh())
        ttk.Button(top, text="Clear", command=self._clear_search).pack(side="left")

        columns = ("id", "name", "class", "section", "phone", "status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for column, heading, width in (
            ("id", "ID", 60), ("name", "Name", 260), ("class", "Class", 120),
            ("section", "Section", 90), ("phone", "Phone", 120), ("status", "Status", 100),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())

        buttons = tk.Frame(self, bg=SPLASH_BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        for text, command in (
            ("Add Student", self.add_student), ("Edit", self.edit_selected),
            ("Deactivate", self.deactivate_selected), ("Mark as Left", self.mark_left_selected),
            ("Bulk Import", self.bulk_import), ("Promote Class", self.promote_class),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=4)

    def _clear_search(self) -> None:
        """Clear the search field and reload students."""
        auth.touch_session()
        self.search_var.set("")
        self.refresh()

    def refresh(self) -> None:
        """Reload the student list from the database."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        with connect_db() as conn:
            rows = conn.execute(
                """
                SELECT id, name, class, section, phone, status
                FROM students
                WHERE name LIKE ? OR class LIKE ? OR aadhaar LIKE ?
                ORDER BY class, name
                """,
                (term, term, term),
            ).fetchall()
        for row in rows:
            self.tree.insert("", "end", iid=str(row["id"]), values=tuple(row))

    def _selected_id(self) -> int | None:
        """Return the selected student id, if any."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Select student", "Please select a student first.")
            return None
        return int(selected[0])

    @auth.require_role("ADMIN")
    def add_student(self) -> None:
        """Open the add-student dialog."""
        AddStudentDialog(self, on_saved=self.refresh)

    @auth.require_role("ADMIN")
    def edit_selected(self) -> None:
        """Open the edit dialog for the selected student."""
        student_id = self._selected_id()
        if student_id is not None:
            EditStudentDialog(self, student_id, on_saved=self.refresh)

    @auth.require_role("ADMIN")
    def deactivate_selected(self) -> None:
        """Deactivate the selected student after warning about unpaid dues."""
        student_id = self._selected_id()
        if student_id is None or not ensure_admin_write():
            return
        with connect_db() as conn:
            due = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM payments WHERE student_id = ?", (student_id,)).fetchone()[0]
            if due and not messagebox.askyesno("Unpaid dues", f"Student has unpaid dues of Rs. {due:,.2f}. Deactivate anyway?"):
                return
            old = dict(conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone())
            conn.execute("UPDATE students SET is_active = 0 WHERE id = ?", (student_id,))
            audit(conn, "STUDENT_DEACTIVATE", "students", student_id, old, {"is_active": 0})
        self.refresh()

    @auth.require_role("ADMIN")
    def mark_left_selected(self) -> None:
        """Mark the selected student as LEFT if no balance remains."""
        student_id = self._selected_id()
        if student_id is None or not ensure_admin_write():
            return
        with connect_db() as conn:
            due = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM payments WHERE student_id = ?", (student_id,)).fetchone()[0]
            if due > 0:
                messagebox.showerror("Cannot mark left", f"Student has unpaid dues of Rs. {due:,.2f}.")
                return
            old = dict(conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone())
            conn.execute("UPDATE students SET status = 'LEFT', is_active = 0 WHERE id = ?", (student_id,))
            audit(conn, "STUDENT_LEFT", "students", student_id, old, {"status": "LEFT", "is_active": 0})
        self.refresh()

    @auth.require_role("ADMIN")
    def bulk_import(self) -> None:
        """Open the bulk-import dialog."""
        BulkImportDialog(self, on_imported=self.refresh)

    @auth.require_role("ADMIN")
    def promote_class(self) -> None:
        """Open the class-promotion dialog."""
        PromoteClassDialog(self, on_saved=self.refresh)


class StudentDialog(tk.Toplevel):
    """Base dialog for adding or editing student rows."""

    def __init__(self, master, title: str, on_saved=None):
        """Initialize common form state."""
        super().__init__(master)
        self.on_saved = on_saved
        self.title(title)
        self.geometry("430x360")
        self.configure(bg=SPLASH_BG)
        self.vars = {key: tk.StringVar() for key in ("name", "class", "section", "aadhaar", "phone", "guardian_name")}
        self._build_form()

    def _classes(self) -> list[str]:
        """Return class names for the active academic year from database rows only."""
        with connect_db() as conn:
            active = conn.execute("SELECT label FROM academic_years WHERE is_active = 1 LIMIT 1").fetchone()
            active_label = active["label"] if active else ""
            rows = conn.execute(
                """
                SELECT DISTINCT class FROM fee_structure WHERE academic_year = ? AND class IS NOT NULL AND class <> ''
                UNION
                SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> ''
                ORDER BY class
                """,
                (active_label,),
            ).fetchall()
        return [row[0] for row in rows]

    def _sections(self) -> list[str]:
        """Return known section values from existing student rows."""
        with connect_db() as conn:
            rows = conn.execute("SELECT DISTINCT section FROM students WHERE section IS NOT NULL AND section <> '' ORDER BY section").fetchall()
        return [row[0] for row in rows]

    def _build_form(self) -> None:
        """Build the student entry form."""
        frame = tk.Frame(self, bg=SPLASH_BG)
        frame.pack(fill="both", expand=True, padx=24, pady=20)
        fields = (
            ("Name", "name"), ("Class", "class"), ("Section", "section"),
            ("Aadhaar", "aadhaar"), ("Phone", "phone"), ("Guardian Name", "guardian_name"),
        )
        for row, (label, key) in enumerate(fields):
            tk.Label(frame, text=label, bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row, column=0, sticky="w", pady=5)
            if key == "class":
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=self._classes(), state="readonly")
            elif key == "section":
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=self._sections())
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            if key == "aadhaar":
                self.aadhaar_entry = widget
        frame.columnconfigure(1, weight=1)
        ttk.Button(frame, text="Save", command=self.save).grid(row=len(fields), column=0, columnspan=2, pady=16)

    def _validate(self, conn: sqlite3.Connection, student_id: int | None = None) -> bool:
        """Validate common student fields before saving."""
        if not self.vars["name"].get().strip():
            messagebox.showerror("Validation", "Name is required.")
            return False
        aadhaar = re.sub(r"\s+", "", self.vars["aadhaar"].get())
        phone = re.sub(r"\D", "", self.vars["phone"].get())
        if not AADHAAR_RE.match(aadhaar):
            messagebox.showerror("Validation", "Aadhaar must be exactly 12 digits.")
            return False
        if not PHONE_RE.match(phone):
            messagebox.showerror("Validation", "Phone must be exactly 10 digits.")
            return False
        params = [aadhaar]
        sql = "SELECT id FROM students WHERE aadhaar = ?"
        if student_id is not None:
            sql += " AND id <> ?"
            params.append(student_id)
        if conn.execute(sql, params).fetchone():
            messagebox.showerror("Validation", "Aadhaar already exists.")
            return False
        self.vars["aadhaar"].set(aadhaar)
        self.vars["phone"].set(phone)
        return True

    def save(self) -> None:
        """Save student data in subclasses."""
        raise NotImplementedError


class AddStudentDialog(StudentDialog):
    """Dialog for creating a new student."""

    def __init__(self, master, on_saved=None):
        """Initialize the add-student dialog."""
        super().__init__(master, "Add Student", on_saved)

    @auth.require_role("ADMIN")
    def save(self) -> None:
        """Insert a validated student row and audit the operation."""
        if not ensure_admin_write():
            return
        with connect_db() as conn:
            if not self._validate(conn):
                return
            cursor = conn.execute(
                """
                INSERT INTO students (name, class, section, aadhaar, phone, guardian_name, is_active, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    self.vars["name"].get().strip(), self.vars["class"].get(), self.vars["section"].get().strip(),
                    self.vars["aadhaar"].get() or None, self.vars["phone"].get() or None,
                    self.vars["guardian_name"].get().strip(), STATUS_ACTIVE, now_str(),
                ),
            )
            audit(conn, "STUDENT_ADD", "students", cursor.lastrowid, None, dict(self.vars_to_values()))
        if self.on_saved:
            self.on_saved()
        self.destroy()

    def vars_to_values(self) -> dict[str, str]:
        """Return current form values for audit logging."""
        return {key: var.get() for key, var in self.vars.items()}


class EditStudentDialog(StudentDialog):
    """Dialog for editing allowed student fields."""

    def __init__(self, master, student_id: int, on_saved=None):
        """Initialize the edit-student dialog."""
        self.student_id = student_id
        super().__init__(master, "Edit Student", on_saved)
        self._load()
        self.aadhaar_entry.configure(state="readonly")

    def _load(self) -> None:
        """Load existing student values into the form."""
        with connect_db() as conn:
            row = conn.execute("SELECT * FROM students WHERE id = ?", (self.student_id,)).fetchone()
        if row:
            for key in self.vars:
                self.vars[key].set(row[key] or "")

    @auth.require_role("ADMIN")
    def save(self) -> None:
        """Update editable student fields and audit old/new values."""
        if not ensure_admin_write():
            return
        with connect_db() as conn:
            if not self._validate(conn, self.student_id):
                return
            old = dict(conn.execute("SELECT * FROM students WHERE id = ?", (self.student_id,)).fetchone())
            new_values = {
                "name": self.vars["name"].get().strip(),
                "class": self.vars["class"].get(),
                "section": self.vars["section"].get().strip(),
                "phone": self.vars["phone"].get() or None,
                "guardian_name": self.vars["guardian_name"].get().strip(),
            }
            conn.execute(
                """
                UPDATE students
                SET name = ?, class = ?, section = ?, phone = ?, guardian_name = ?
                WHERE id = ?
                """,
                (*new_values.values(), self.student_id),
            )
            audit(conn, "STUDENT_EDIT", "students", self.student_id, old, new_values)
        if self.on_saved:
            self.on_saved()
        self.destroy()


class BulkImportDialog(tk.Toplevel):
    """Preview and import school-specific student Excel files."""

    def __init__(self, master, on_imported=None):
        """Create the bulk-import preview window."""
        super().__init__(master)
        self.on_imported = on_imported
        self.rows: list[dict] = []
        self.title("Bulk Import Students")
        self.geometry("980x560")
        self.configure(bg=SPLASH_BG)
        self.file_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="Select an .xlsx file to preview.")
        self._build_widgets()

    def _build_widgets(self) -> None:
        """Build file picker, preview grid, and import controls."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Entry(top, textvariable=self.file_var, width=80).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Browse", command=self.browse).pack(side="left")
        ttk.Button(top, text="Preview", command=self.preview).pack(side="left", padx=6)
        columns = ("sl", "name", "class", "dob", "phone", "aadhaar", "status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for column, heading, width in (
            ("sl", "SL", 60), ("name", "Name", 220), ("class", "Class", 120), ("dob", "DOB", 100),
            ("phone", "Phone", 110), ("aadhaar", "Aadhaar", 130), ("status", "Status", 260),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width)
        self.tree.tag_configure("ok", foreground="green")
        self.tree.tag_configure("error", foreground="red")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        bottom = tk.Frame(self, bg=SPLASH_BG)
        bottom.pack(fill="x", padx=12, pady=10)
        tk.Label(bottom, textvariable=self.summary_var, bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        ttk.Button(bottom, text="Import Valid Rows", command=self.import_valid_rows).pack(side="right")

    def browse(self) -> None:
        """Select an Excel workbook for import."""
        auth.touch_session()
        path = filedialog.askopenfilename(filetypes=(("Excel workbooks", "*.xlsx"),))
        if path:
            self.file_var.set(path)

    def preview(self) -> None:
        """Parse and preview import rows from the chosen workbook."""
        auth.touch_session()
        from openpyxl import load_workbook

        path = self.file_var.get().strip()
        if not path:
            messagebox.showwarning("Select file", "Please select an .xlsx file.")
            return
        workbook = load_workbook(path, read_only=True, data_only=True)
        if "full detail 100" not in workbook.sheetnames:
            messagebox.showerror("Invalid file", "Expected sheet 'full detail 100'.")
            return
        self.rows = self._parse_sheet(workbook["full detail 100"])
        self._render_preview()

    def _parse_sheet(self, sheet) -> list[dict]:
        """Parse all current-year class sections from the Excel sheet."""
        parsed = []
        current_class = None
        headers = None
        aadhaar_seen = set()
        with connect_db() as conn:
            existing_aadhaar = {row[0] for row in conn.execute("SELECT aadhaar FROM students WHERE aadhaar IS NOT NULL AND aadhaar <> ''")}
        for row in sheet.iter_rows(values_only=True):
            values = list(row)
            first_cells = [str(value).strip() for value in values[:5] if value not in (None, "")]
            section_title = " ".join(first_cells)
            if "26-27" in section_title:
                if "OLD" in section_title.upper() or "25-26" in section_title:
                    current_class = None
                else:
                    current_class = normalize_class(section_title.replace("26-27", "").strip())
                headers = None
                continue
            if current_class is None:
                continue
            lowered = [str(value).strip().lower() if value is not None else "" for value in values]
            if any("student" in value and "name" in value for value in lowered):
                headers = {name: index for index, name in enumerate(lowered) if name}
                continue
            if headers is None:
                continue
            item = build_import_row(values, headers, current_class, existing_aadhaar, aadhaar_seen)
            if item:
                parsed.append(item)
                if item["aadhaar"]:
                    aadhaar_seen.add(item["aadhaar"])
        return parsed

    def _render_preview(self) -> None:
        """Render parsed rows into the preview tree."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        valid = 0
        errors = 0
        for index, row in enumerate(self.rows, start=1):
            ok = row["status"] == "OK"
            valid += 1 if ok else 0
            errors += 0 if ok else 1
            self.tree.insert(
                "", "end", values=(index, row["name"], row["class"], row["dob"], row["phone"], row["aadhaar"], row["status"]),
                tags=("ok" if ok else "error",),
            )
        self.summary_var.set(f"{valid} valid, {errors} errors")

    @auth.require_role("ADMIN")
    def import_valid_rows(self) -> None:
        """Insert only preview rows that passed validation."""
        if not ensure_admin_write():
            return
        valid_rows = [row for row in self.rows if row["status"] == "OK"]
        skipped = len(self.rows) - len(valid_rows)
        imported = 0
        with connect_db() as conn:
            for row in valid_rows:
                cursor = conn.execute(
                    """
                    INSERT INTO students (
                        name, class, section, aadhaar, phone, guardian_name, is_active, status, created_at,
                        dob, gender, category, route, vehicle_fee, has_vehicle_fee
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["name"], row["class"], row["section"], row["aadhaar"], row["phone"], row["guardian_name"],
                        STATUS_ACTIVE, now_str(), row["dob"], row["gender"], row["category"], row["route"],
                        row["vehicle_fee"], 1 if row["has_vehicle_fee"] else 0,
                    ),
                )
                audit(conn, "BULK_IMPORT", "students", cursor.lastrowid, None, row)
                imported += 1
        if imported and self._should_seed_fee_structure():
            self._seed_fee_structure()
        messagebox.showinfo("Bulk import", f"Imported {imported} students. Skipped {skipped} rows with errors.")
        if self.on_imported:
            self.on_imported()
        self.destroy()

    def _should_seed_fee_structure(self) -> bool:
        """Return True when fee_structure for 2026-27 is empty and user confirms seeding."""
        with connect_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM fee_structure WHERE academic_year = ?", ("2026-27",)).fetchone()[0]
        return count == 0 and messagebox.askyesno("Seed fee structure", "Also seed fee structure for 2026-27?")

    def _seed_fee_structure(self) -> None:
        """Seed standard 2026-27 fee heads and class fee amounts."""
        if not ensure_admin_write():
            return
        with connect_db() as conn:
            head_ids = {}
            for name, register_type in FEE_HEADS:
                row = conn.execute("SELECT id FROM fee_heads WHERE name = ?", (name,)).fetchone()
                if row:
                    head_ids[name] = row["id"]
                else:
                    cursor = conn.execute("INSERT INTO fee_heads (name, register_type, is_active) VALUES (?, ?, 1)", (name, register_type))
                    head_ids[name] = cursor.lastrowid
                    audit(conn, "FEE_HEAD_ADD", "fee_heads", cursor.lastrowid, None, {"name": name, "register_type": register_type})
            regular_heads = ["Admission Fee", "Tuition Fee", "Term Exam Fee", "Computer Fee", "Sports & Activity Fee"]
            for class_name, amounts in FEE_SEED.items():
                for head_name, amount in zip(regular_heads, amounts):
                    conn.execute(
                        "INSERT INTO fee_structure (academic_year, class, fee_head_id, amount, due_date) VALUES (?, ?, ?, ?, ?)",
                        ("2026-27", class_name, head_ids[head_name], amount, ""),
                    )
            for route, amount in VEHICLE_FEES.items():
                conn.execute(
                    "INSERT INTO fee_structure (academic_year, class, fee_head_id, amount, due_date) VALUES (?, ?, ?, ?, ?)",
                    ("2026-27", route, head_ids["Vehicle Fee"], amount, ""),
                )
            audit(conn, "FEE_STRUCTURE_SEED", "fee_structure", "2026-27", None, {"academic_year": "2026-27"})


class PromoteClassDialog(tk.Toplevel):
    """Dialog for promoting selected students from one class to another."""

    def __init__(self, master, on_saved=None):
        """Create the promotion dialog."""
        super().__init__(master)
        self.on_saved = on_saved
        self.title("Promote Class")
        self.geometry("520x500")
        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self._build_widgets()

    def _classes(self) -> list[str]:
        """Return classes that currently have students."""
        with connect_db() as conn:
            return [row[0] for row in conn.execute("SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> '' ORDER BY class")]

    def _build_widgets(self) -> None:
        """Build class selectors and student checklist."""
        top = tk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)
        classes = self._classes()
        ttk.Combobox(top, textvariable=self.source_var, values=classes, state="readonly").pack(side="left", padx=4)
        ttk.Combobox(top, textvariable=self.target_var, values=classes, state="readonly").pack(side="left", padx=4)
        ttk.Button(top, text="Load", command=self.load_students).pack(side="left", padx=4)
        self.tree = ttk.Treeview(self, columns=("selected", "id", "name"), show="headings")
        for column in ("selected", "id", "name"):
            self.tree.heading(column, text=column.title())
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        self.tree.bind("<Double-1>", self.toggle_selected)
        ttk.Button(self, text="Confirm Promotion", command=self.confirm).pack(pady=10)

    def load_students(self) -> None:
        """Load students from the selected source class."""
        auth.touch_session()
        for item in self.tree.get_children():
            self.tree.delete(item)
        with connect_db() as conn:
            rows = conn.execute("SELECT id, name FROM students WHERE class = ? AND is_active = 1 ORDER BY name", (self.source_var.get(),)).fetchall()
        for row in rows:
            self.tree.insert("", "end", iid=str(row["id"]), values=("Yes", row["id"], row["name"]))

    def toggle_selected(self, _event) -> None:
        """Toggle whether the highlighted student will be promoted."""
        auth.touch_session()
        item = self.tree.focus()
        if item:
            values = list(self.tree.item(item, "values"))
            values[0] = "No" if values[0] == "Yes" else "Yes"
            self.tree.item(item, values=values)

    @auth.require_role("ADMIN")
    def confirm(self) -> None:
        """Promote selected students to the target class and audit each update."""
        if not ensure_admin_write():
            return
        source = self.source_var.get()
        target = self.target_var.get()
        if not source or not target:
            messagebox.showerror("Promotion", "Select source and target classes.")
            return
        selected_ids = [int(item) for item in self.tree.get_children() if self.tree.item(item, "values")[0] == "Yes"]
        with connect_db() as conn:
            for student_id in selected_ids:
                old = dict(conn.execute("SELECT id, name, class FROM students WHERE id = ?", (student_id,)).fetchone())
                conn.execute("UPDATE students SET class = ? WHERE id = ?", (target, student_id))
                audit(conn, "CLASS_PROMOTION", "students", student_id, old, {"class": target})
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo("Promotion", f"Promoted {len(selected_ids)} students from {source} to {target}.")
        self.destroy()


def normalize_class(value) -> str:
    """Normalize school-specific class names to SFMS class labels."""
    text = re.sub(r"\s+", " ", str(value).upper().replace(".", " ")).strip()
    return CLASS_MAP.get(text, text.title())


def extract_phone(value) -> str:
    """Extract the first 10-digit phone number from a cell value."""
    digits = re.sub(r"\D", "", str(value or ""))
    match = re.search(r"\d{10}", digits)
    return match.group(0) if match else ""


def normalize_date(value) -> str:
    """Normalize Excel date values and common date strings to DD-MM-YYYY text."""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=int(value))).strftime("%d-%m-%Y")
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return text


def normalize_gender(value) -> str:
    """Normalize Girl/Boy values to F/M."""
    text = str(value or "").strip().upper()
    if text == "BOY":
        return "M"
    if text == "GIRL":
        return "F"
    return ""


def normalize_category(value) -> str:
    """Normalize caste/category values to SC/ST/OBC/GEN when recognized."""
    text = str(value or "").strip().upper()
    return text if text in {"SC", "ST", "OBC", "GEN"} else text


def route_from_address(address) -> tuple[str, int]:
    """Return route code and annual vehicle fee from an address cell."""
    text = str(address or "").upper()
    if "KAMTON" in text or "KAMTONE" in text:
        return "KAMTONE", 3600
    if "PIPARIYA" in text or "SALAIYA" in text or "CHEENDMOD" in text:
        return "PIPARIYA", 4200
    return "BARELI", 3000


def header_value(values: list, headers: dict[str, int], *candidates: str):
    """Return the value for the first matching header candidate."""
    for candidate in candidates:
        candidate_lower = candidate.lower()
        for header, index in headers.items():
            if candidate_lower == header or candidate_lower in header:
                return values[index] if index < len(values) else None
    return None


def build_import_row(values: list, headers: dict[str, int], class_name: str, existing_aadhaar: set, aadhaar_seen: set) -> dict | None:
    """Build and validate a single bulk-import preview row."""
    name = str(header_value(values, headers, "Student Name") or "").strip()
    if not name:
        return None
    aadhaar = re.sub(r"\s+", "", str(header_value(values, headers, "AADHAAR CARD NO") or ""))
    phone = extract_phone(header_value(values, headers, "Mob.No.1", "Mob"))
    address = header_value(values, headers, "Address")
    route, vehicle_fee = route_from_address(address)
    has_vehicle_fee = bool(str(header_value(values, headers, "conveyance") or "").strip())
    errors = []
    if not AADHAAR_RE.match(aadhaar):
        errors.append("invalid Aadhaar")
    elif aadhaar in existing_aadhaar or aadhaar in aadhaar_seen:
        errors.append("duplicate Aadhaar")
    if not PHONE_RE.match(phone):
        errors.append("invalid phone")
    status = "OK" if not errors else "ERROR: " + ", ".join(errors)
    return {
        "name": name,
        "class": class_name,
        "section": "",
        "dob": normalize_date(header_value(values, headers, "D.O.B", "DOB")),
        "phone": phone,
        "aadhaar": aadhaar,
        "guardian_name": str(header_value(values, headers, "Father's Name", "Father") or "").strip(),
        "gender": normalize_gender(header_value(values, headers, "Girl/Boy")),
        "category": normalize_category(header_value(values, headers, "Category")),
        "route": route,
        "vehicle_fee": vehicle_fee if has_vehicle_fee else 0,
        "has_vehicle_fee": has_vehicle_fee,
        "status": status,
    }
