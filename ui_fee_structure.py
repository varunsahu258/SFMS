"""Fee-structure management screen for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from ui_date import DateEntry
from config import SPLASH_BG, SPLASH_FG
from ledger import ensure_student_charges
from installment_service import (
    get_installment_schedule, installment_amounts, save_installment_schedule,
    validate_installment_dates,
)
from ui_master_utils import audit, connect_db, ensure_permission_write
from utils import format_currency


class FeeStructureWindow(WorkspacePage):
    """Window for editing fee amounts and due dates by academic year and class."""

    def __init__(self, master=None, *, embedded: bool = False):
        """Create the fee-structure window."""
        super().__init__(master, embedded=embedded)
        self.title("Fee Structure")
        self.geometry("820x520")
        self.configure(bg=SPLASH_BG)
        self.year_var = tk.StringVar()
        self.class_var = tk.StringVar()
        self.increase_var = tk.StringVar(value="0")
        self.amount_vars: dict[int, tk.StringVar] = {}
        self.due_vars: dict[int, tk.StringVar] = {}
        self.installment_vars = [tk.StringVar() for _index in range(3)]
        self._build_widgets()
        self._load_filters()

    def _build_widgets(self) -> None:
        """Build selectors, editable grid, and buttons."""
        top = tk.Frame(self, bg=SPLASH_BG)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="Academic Year", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left")
        self.year_combo = ttk.Combobox(top, textvariable=self.year_var, state="readonly", width=14)
        self.year_combo.pack(side="left", padx=6)
        tk.Label(top, text="Class", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(12, 0))
        self.class_combo = ttk.Combobox(top, textvariable=self.class_var, state="readonly", width=18)
        self.class_combo.pack(side="left", padx=6)
        ttk.Button(top, text="Load", command=self.load_grid).pack(side="left", padx=6)
        tk.Label(top, text="% Increase", bg=SPLASH_BG, fg=SPLASH_FG).pack(side="left", padx=(12, 0))
        ttk.Entry(top, textvariable=self.increase_var, width=8).pack(side="left", padx=6)
        ttk.Button(top, text="Copy from previous year", command=self.copy_previous_year).pack(side="left", padx=6)

        schedule = ttk.LabelFrame(self, text="Three-installment schedule (48% / 26% / 26%)", padding=10)
        schedule.pack(fill="x", padx=12, pady=(2, 6))
        for index, variable in enumerate(self.installment_vars, start=1):
            ttk.Label(schedule, text=f"Installment {index} due").pack(side="left", padx=(0 if index == 1 else 12, 4))
            DateEntry(schedule, textvariable=variable, width=12).pack(side="left")
        self.installment_preview = ttk.Label(schedule, text="", style="Muted.TLabel")
        self.installment_preview.pack(side="right")

        self.grid_frame = tk.Frame(self, bg=SPLASH_BG)
        self.grid_frame.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Button(self, text="Save", command=self.save).pack(pady=10)

    def _load_filters(self) -> None:
        """Load academic-year and class dropdown values from the database."""
        with connect_db() as conn:
            years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label")]
            classes = [row[0] for row in conn.execute(
                """
                SELECT DISTINCT class FROM students WHERE class IS NOT NULL AND class <> ''
                UNION SELECT DISTINCT class FROM fee_structure WHERE class IS NOT NULL AND class <> ''
                ORDER BY class
                """
            )]
        self.year_combo.configure(values=years)
        self.class_combo.configure(values=classes)
        if years:
            self.year_var.set(years[-1])
        if classes:
            self.class_var.set(classes[0])

    def _fee_heads(self) -> list:
        """Return active fee heads for the grid."""
        with connect_db() as conn:
            return conn.execute(
                "SELECT id, name FROM fee_heads WHERE is_active=1 AND COALESCE(is_one_time,0)=0 ORDER BY name"
            ).fetchall()

    def load_grid(self) -> None:
        """Load fee-head rows and existing values into editable entries."""
        auth.touch_session()
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.amount_vars.clear()
        self.due_vars.clear()
        headers = ("Fee Head", "Amount", "Due Date")
        for column, header in enumerate(headers):
            tk.Label(self.grid_frame, text=header, bg=SPLASH_BG, fg=SPLASH_FG, font=("Segoe UI", 10, "bold")).grid(row=0, column=column, sticky="ew", padx=4, pady=4)
        with connect_db() as conn:
            schedule = get_installment_schedule(conn, self.year_var.get(), self.class_var.get())
            for index, variable in enumerate(self.installment_vars, start=1):
                variable.set(schedule[f"installment_{index}_due"] if schedule else "")
            existing = {
                row["fee_head_id"]: row
                for row in conn.execute(
                    "SELECT fee_head_id, amount, due_date FROM fee_structure WHERE academic_year = ? AND class = ?",
                    (self.year_var.get(), self.class_var.get()),
                )
            }
        for row_index, head in enumerate(self._fee_heads(), start=1):
            amount_var = tk.StringVar(value=str(existing.get(head["id"], {}).get("amount", "")))
            due_var = tk.StringVar(value=str(existing.get(head["id"], {}).get("due_date", "")))
            self.amount_vars[head["id"]] = amount_var
            self.due_vars[head["id"]] = due_var
            amount_var.trace_add("write", lambda *_args: self._update_installment_preview())
            tk.Label(self.grid_frame, text=head["name"], bg=SPLASH_BG, fg=SPLASH_FG).grid(row=row_index, column=0, sticky="w", padx=4, pady=4)
            ttk.Entry(self.grid_frame, textvariable=amount_var).grid(row=row_index, column=1, sticky="ew", padx=4, pady=4)
            DateEntry(self.grid_frame, textvariable=due_var, width=15).grid(row=row_index, column=2, sticky="ew", padx=4, pady=4)
        self.grid_frame.columnconfigure(1, weight=1)
        self.grid_frame.columnconfigure(2, weight=1)
        self._update_installment_preview()

    def _update_installment_preview(self) -> None:
        """Show the fixed installment amounts for the currently entered annual total."""
        try:
            total = sum(float(variable.get() or 0) for variable in self.amount_vars.values())
            first, second, third = installment_amounts(total)
            text = (f"Annual total {format_currency(total)}  •  "
                    f"Installments: {format_currency(first)} / {format_currency(second)} / {format_currency(third)}")
        except ValueError:
            text = "Enter numeric fee amounts to preview installments."
        self.installment_preview.configure(text=text)

    def copy_previous_year(self) -> None:
        """Copy same-class amounts from the previous academic year with optional increase."""
        auth.touch_session()
        current = self.year_var.get()
        if not current or "-" not in current:
            return
        start = int(current.split("-")[0])
        previous = f"{start - 1}-{str(start)[-2:]}"
        try:
            increase = float(self.increase_var.get() or 0)
        except ValueError:
            messagebox.showerror("Validation", "Percent increase must be numeric.")
            return
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT fee_head_id, amount, due_date FROM fee_structure WHERE academic_year = ? AND class = ?",
                (previous, self.class_var.get()),
            ).fetchall()
        if not rows:
            messagebox.showinfo("Copy", "No previous-year fee structure was found for this class.")
            return
        self.load_grid()
        for row in rows:
            if row["fee_head_id"] in self.amount_vars:
                amount = float(row["amount"] or 0) * (1 + increase / 100)
                self.amount_vars[row["fee_head_id"]].set(f"{amount:.2f}")
                self.due_vars[row["fee_head_id"]].set(row["due_date"] or "")

    @auth.require_permission("manage_fee_structure")
    def save(self) -> None:
        """Update or insert all edited fee-structure rows."""
        if not ensure_permission_write("manage_fee_structure"):
            return
        academic_year = self.year_var.get()
        class_name = self.class_var.get()
        if not academic_year or not class_name:
            messagebox.showerror("Validation", "Select academic year and class.")
            return
        dates = tuple(variable.get().strip() for variable in self.installment_vars)
        if any(dates) and not all(dates):
            messagebox.showerror("Installments", "Set all three installment due dates, or leave all three blank.")
            return
        if all(dates):
            try:
                validate_installment_dates(dates)
            except ValueError as exc:
                messagebox.showerror("Installments", str(exc))
                return
        with connect_db() as conn:
            for fee_head_id, amount_var in self.amount_vars.items():
                amount_text = amount_var.get().strip()
                if amount_text == "":
                    continue
                try:
                    amount = float(amount_text)
                except ValueError:
                    messagebox.showerror("Validation", "All amounts must be numeric.")
                    return
                due_date = self.due_vars[fee_head_id].get().strip()
                row = conn.execute(
                    "SELECT id FROM fee_structure WHERE academic_year = ? AND class = ? AND fee_head_id = ?",
                    (academic_year, class_name, fee_head_id),
                ).fetchone()
                new_values = {"academic_year": academic_year, "class": class_name, "fee_head_id": fee_head_id, "amount": amount, "due_date": due_date}
                if row:
                    old = dict(conn.execute("SELECT * FROM fee_structure WHERE id = ?", (row["id"],)).fetchone())
                    charge_count = conn.execute("SELECT COUNT(*) FROM student_charges WHERE fee_structure_id=?", (row["id"],)).fetchone()[0]
                    if charge_count and (float(old["amount"] or 0) != amount or str(old["due_date"] or "") != due_date):
                        messagebox.showerror("Fee Structure", "This fee structure already has issued student charges and cannot be edited. Create a new academic-year structure instead.")
                        return
                    conn.execute("UPDATE fee_structure SET amount = ?, due_date = ? WHERE id = ?", (amount, due_date, row["id"]))
                    audit(conn, "FEE_STRUCTURE_EDIT", "fee_structure", row["id"], old, new_values)
                else:
                    cursor = conn.execute(
                        "INSERT INTO fee_structure (academic_year, class, fee_head_id, amount, due_date) VALUES (?, ?, ?, ?, ?)",
                        (academic_year, class_name, fee_head_id, amount, due_date),
                    )
                    audit(conn, "FEE_STRUCTURE_ADD", "fee_structure", cursor.lastrowid, None, new_values)
            if all(dates):
                save_installment_schedule(
                    conn, academic_year, class_name, dates,
                    auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None,
                )
                audit(conn, "INSTALLMENT_SCHEDULE_SAVE", "installment_schedules",
                      f"{academic_year}:{class_name}", new={"dates": dates, "percentages": [48, 26, 26]})
            active = conn.execute("SELECT 1 FROM academic_years WHERE label=? AND is_active=1", (academic_year,)).fetchone()
            if active:
                ensure_student_charges(conn, academic_year)
        messagebox.showinfo("Fee Structure", "Fee structure saved.")
