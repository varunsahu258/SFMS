"""Automatic timetable setup, generation, editing, viewing, and exports."""

from __future__ import annotations

import os
from queue import Empty, Queue
import sqlite3
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import auth
from config import DB_PATH, SPLASH_BG, SPLASH_FG
from timetable_db import (
    CONSTRAINT_TYPES, build_problem, create_version, delete_assignment, delete_requirement,
    delete_subject, delete_teacher, get_schedule_config, list_assignments, list_requirements,
    list_subjects, list_teacher_availability, list_teacher_constraints, list_teachers,
    list_timetable, list_versions, period_times, publish_version, save_assignment,
    save_requirement, save_schedule_config, save_subject, save_teacher,
    save_teacher_availability, save_teacher_constraints, save_timetable_cell,
    save_timetable_slots, set_cell_lock, timetable_classes,
)
from timetable_report import class_timetable_pdf, master_timetable_pdf, teacher_duty_pdf, timetable_excel
from timetable_solver import solve
from ui_theme import apply_theme
from ui_workspace import WorkspacePage

PASTELS = ("#e8f1ff", "#e5f7ec", "#fff0e3", "#f4e8ff", "#fff8d9", "#e5f7f6")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _themed_toplevel(master, title: str, geometry: str) -> tk.Toplevel:
    window = tk.Toplevel(master)
    window.title(title)
    window.geometry(geometry)
    apply_theme(window)
    return window


def _selected_id(combo: ttk.Combobox, mapping: dict[str, int], label: str) -> int:
    value = combo.get().strip()
    if value not in mapping:
        raise ValueError(f"Select {label}.")
    return mapping[value]


class SubjectWindow:
    @auth.require_permission("manage_timetable")
    def __init__(self, master):
        self.window = _themed_toplevel(master, "Timetable Subjects", "700x500")
        self.name, self.code = tk.StringVar(), tk.StringVar()
        self.lab, self.active = tk.BooleanVar(), tk.BooleanVar(value=True)
        self.selected_id = None
        form = ttk.Frame(self.window, padding=12); form.pack(fill="x")
        for row, (text, variable) in enumerate((("Name", self.name), ("Code", self.code))):
            ttk.Label(form, text=text).grid(row=row, column=0, sticky="w", padx=4, pady=5)
            ttk.Entry(form, textvariable=variable, width=30).grid(row=row, column=1, sticky="w", padx=4)
        ttk.Checkbutton(form, text="Laboratory subject", variable=self.lab).grid(row=0, column=2, padx=12)
        ttk.Checkbutton(form, text="Active", variable=self.active).grid(row=1, column=2, padx=12)
        ttk.Button(form, text="Save", command=self.save).grid(row=0, column=3, rowspan=2, padx=8)
        ttk.Button(form, text="Clear", command=self.clear).grid(row=0, column=4, rowspan=2)
        self.tree = ttk.Treeview(self.window, columns=("name", "code", "lab", "active"), show="headings")
        for column, text, width in (("name", "Subject", 230), ("code", "Code", 110), ("lab", "Lab", 70), ("active", "Active", 70)):
            self.tree.heading(column, text=text); self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        ttk.Button(self.window, text="Delete Selected", command=self.delete).pack(pady=(0, 12))
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        with _connect() as conn:
            for row in list_subjects(conn):
                self.tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["code"], "Yes" if row["is_lab"] else "No", "Yes" if row["is_active"] else "No"))

    def select(self, _event=None):
        selection = self.tree.selection()
        if not selection: return
        self.selected_id = int(selection[0]); values = self.tree.item(selection[0], "values")
        self.name.set(values[0]); self.code.set(values[1]); self.lab.set(values[2] == "Yes"); self.active.set(values[3] == "Yes")

    def clear(self):
        self.selected_id = None; self.name.set(""); self.code.set(""); self.lab.set(False); self.active.set(True)

    def save(self):
        auth.touch_session()
        try:
            with _connect() as conn:
                save_subject(conn, {"id": self.selected_id, "name": self.name.get(), "code": self.code.get(), "is_lab": self.lab.get(), "is_active": self.active.get()})
                conn.commit()
            self.clear(); self.refresh()
        except Exception as exc: messagebox.showerror("Subjects", str(exc), parent=self.window)

    def delete(self):
        if not self.selected_id: return
        if not messagebox.askyesno("Subjects", "Delete the selected subject and related setup?", parent=self.window): return
        try:
            with _connect() as conn: delete_subject(conn, self.selected_id); conn.commit()
            self.clear(); self.refresh()
        except Exception as exc: messagebox.showerror("Subjects", str(exc), parent=self.window)


class AvailabilityWindow:
    @auth.require_permission("manage_timetable")
    def __init__(self, master, teacher_id: int):
        self.window = _themed_toplevel(master, "Teacher Availability", "620x430")
        self.teacher_id = teacher_id; self.rows = {}
        with _connect() as conn:
            config = get_schedule_config(conn); existing = {row["day"]: row for row in list_teacher_availability(conn, teacher_id)}
        days = [day for day in config["working_days"].split(",") if day]
        frame = ttk.Frame(self.window, padding=16); frame.pack(fill="both", expand=True)
        for column, text in enumerate(("Day", "Present", "Arrives", "Departs")): ttk.Label(frame, text=text).grid(row=0, column=column, padx=8, pady=6)
        for index, day in enumerate(days, 1):
            present = tk.BooleanVar(value=day in existing); arrives = tk.StringVar(value=existing.get(day, {}).get("arrives", "08:00")); departs = tk.StringVar(value=existing.get(day, {}).get("departs", "14:00"))
            ttk.Label(frame, text=day).grid(row=index, column=0, padx=8, pady=5)
            check = ttk.Checkbutton(frame, variable=present); check.grid(row=index, column=1)
            arrive_entry = ttk.Entry(frame, textvariable=arrives, width=10); arrive_entry.grid(row=index, column=2)
            depart_entry = ttk.Entry(frame, textvariable=departs, width=10); depart_entry.grid(row=index, column=3)
            def toggle(*_args, p=present, a=arrive_entry, d=depart_entry):
                state = "normal" if p.get() else "disabled"; a.configure(state=state); d.configure(state=state)
            present.trace_add("write", toggle); toggle(); self.rows[day] = (present, arrives, departs)
        ttk.Button(frame, text="Save Availability", command=self.save).grid(row=len(days) + 1, column=0, columnspan=4, pady=16)

    def save(self):
        rows = [{"day": day, "arrives": arrives.get().strip(), "departs": departs.get().strip()} for day, (present, arrives, departs) in self.rows.items() if present.get()]
        try:
            with _connect() as conn: save_teacher_availability(conn, self.teacher_id, rows); conn.commit()
            self.window.destroy()
        except Exception as exc: messagebox.showerror("Availability", str(exc), parent=self.window)


class ConstraintsWindow:
    @auth.require_permission("manage_timetable")
    def __init__(self, master, teacher_id: int):
        self.window = _themed_toplevel(master, "Teacher Constraints", "900x560")
        self.teacher_id = teacher_id; self.variables = {}
        with _connect() as conn:
            config = get_schedule_config(conn); existing = {(row["day"], int(row["period_no"])): row["ctype"] for row in list_teacher_constraints(conn, teacher_id)}
        days = [day for day in config["working_days"].split(",") if day]; periods = range(1, int(config["periods_per_day"]) + 1)
        frame = ttk.Frame(self.window, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Period").grid(row=0, column=0)
        for column, day in enumerate(days, 1): ttk.Label(frame, text=day).grid(row=0, column=column, padx=3)
        for row_index, period in enumerate(periods, 1):
            ttk.Label(frame, text=f"P{period}").grid(row=row_index, column=0, pady=4)
            for column, day in enumerate(days, 1):
                variable = tk.StringVar(value=existing.get((day, period), "")); self.variables[(day, period)] = variable
                ttk.Combobox(frame, textvariable=variable, values=("",) + CONSTRAINT_TYPES, state="readonly", width=16).grid(row=row_index, column=column, padx=2)
        ttk.Button(frame, text="Save Constraints", command=self.save).grid(row=int(config["periods_per_day"]) + 2, column=0, columnspan=len(days) + 1, pady=15)

    def save(self):
        rows = [{"day": day, "period_no": period, "ctype": variable.get()} for (day, period), variable in self.variables.items() if variable.get()]
        try:
            with _connect() as conn: save_teacher_constraints(conn, self.teacher_id, rows); conn.commit()
            self.window.destroy()
        except Exception as exc: messagebox.showerror("Constraints", str(exc), parent=self.window)


class TeacherWindow:
    @auth.require_permission("manage_timetable")
    def __init__(self, master):
        self.window = _themed_toplevel(master, "Timetable Teachers", "850x560")
        self.name, self.phone, self.maximum, self.minimum_free = tk.StringVar(), tk.StringVar(), tk.StringVar(value="6"), tk.StringVar(value="1")
        self.active = tk.BooleanVar(value=True); self.selected_id = None
        form = ttk.Frame(self.window, padding=12); form.pack(fill="x")
        for column, (text, variable) in enumerate((("Name", self.name), ("Phone", self.phone), ("Max/day", self.maximum), ("Min free/day", self.minimum_free))):
            ttk.Label(form, text=text).grid(row=0, column=column * 2, padx=4); ttk.Entry(form, textvariable=variable, width=20).grid(row=0, column=column * 2 + 1, padx=4)
        ttk.Checkbutton(form, text="Active", variable=self.active).grid(row=1, column=0, pady=8)
        ttk.Button(form, text="Save", command=self.save).grid(row=1, column=1); ttk.Button(form, text="Availability", command=self.availability).grid(row=1, column=2); ttk.Button(form, text="Constraints", command=self.constraints).grid(row=1, column=3); ttk.Button(form, text="Delete", command=self.delete).grid(row=1, column=4)
        self.tree = ttk.Treeview(self.window, columns=("name", "phone", "max", "free", "active"), show="headings")
        for column, text, width in (("name", "Teacher", 240), ("phone", "Phone", 130), ("max", "Max/day", 90), ("free", "Min free/day", 100), ("active", "Active", 80)):
            self.tree.heading(column, text=text); self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=12, pady=8); self.tree.bind("<<TreeviewSelect>>", self.select); self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        with _connect() as conn:
            for row in list_teachers(conn): self.tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["phone"], row["max_periods_day"], row.get("min_free_periods_day", 1), "Yes" if row["is_active"] else "No"))

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected: return
        self.selected_id = int(selected[0]); values = self.tree.item(selected[0], "values"); self.name.set(values[0]); self.phone.set(values[1]); self.maximum.set(values[2]); self.minimum_free.set(values[3]); self.active.set(values[4] == "Yes")

    def save(self):
        try:
            with _connect() as conn: self.selected_id = save_teacher(conn, {"id": self.selected_id, "name": self.name.get(), "phone": self.phone.get(), "max_periods_day": self.maximum.get(), "min_free_periods_day": self.minimum_free.get(), "is_active": self.active.get()}); conn.commit()
            self.refresh()
        except Exception as exc: messagebox.showerror("Teachers", str(exc), parent=self.window)

    def availability(self):
        if not self.selected_id: messagebox.showwarning("Teachers", "Save or select a teacher first.", parent=self.window); return
        AvailabilityWindow(self.window, self.selected_id)

    def constraints(self):
        if not self.selected_id: messagebox.showwarning("Teachers", "Save or select a teacher first.", parent=self.window); return
        ConstraintsWindow(self.window, self.selected_id)

    def delete(self):
        if not self.selected_id or not messagebox.askyesno("Teachers", "Delete selected teacher?", parent=self.window): return
        try:
            with _connect() as conn: delete_teacher(conn, self.selected_id); conn.commit()
            self.selected_id = None; self.refresh()
        except Exception as exc: messagebox.showerror("Teachers", str(exc), parent=self.window)


class ScheduleConfigWindow:
    FIELDS = (("Periods/day", "periods_per_day"), ("Working days", "working_days"), ("Period minutes", "period_duration_min"), ("Start HH:MM", "day_start_time"), ("Assembly after (0 = before P1)", "assembly_after_period"), ("Assembly minutes", "assembly_duration_min"), ("Break after", "break_after_period"), ("Break minutes", "break_duration_min"), ("Lunch after", "lunch_after_period"), ("Lunch minutes", "lunch_duration_min"))
    def __init__(self, master):
        self.window = _themed_toplevel(master, "Schedule Configuration", "760x600"); self.vars = {}
        with _connect() as conn: config = get_schedule_config(conn)
        frame = ttk.Frame(self.window, padding=16); frame.pack(fill="both", expand=True)
        for row, (label, key) in enumerate(self.FIELDS):
            variable = tk.StringVar(value="" if config.get(key) is None else str(config.get(key))); self.vars[key] = variable
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4); ttk.Entry(frame, textvariable=variable, width=38).grid(row=row, column=1, sticky="w")
            variable.trace_add("write", self.preview)
        ttk.Button(frame, text="Save Configuration", command=self.save).grid(row=0, column=2, padx=12)
        self.preview_text = tk.Text(frame, width=38, height=18, state="disabled"); self.preview_text.grid(row=1, column=2, rowspan=8, padx=12, sticky="nsew"); self.preview()

    def values(self): return {key: variable.get().strip() for key, variable in self.vars.items()}
    def preview(self, *_args):
        try: lines = [f"Period {index}: {start} – {end}" for index, (start, end) in enumerate(period_times(self.values()), 1)]
        except Exception as exc: lines = [str(exc)]
        self.preview_text.configure(state="normal"); self.preview_text.delete("1.0", "end"); self.preview_text.insert("1.0", "\n".join(lines)); self.preview_text.configure(state="disabled")
    def save(self):
        try:
            with _connect() as conn: save_schedule_config(conn, self.values()); conn.commit()
            self.window.destroy()
        except Exception as exc: messagebox.showerror("Schedule", str(exc), parent=self.window)


class AssignmentWindow:
    @auth.require_permission("manage_timetable")
    def __init__(self, master):
        self.window = _themed_toplevel(master, "Assignments & Requirements", "1000x650")
        self.teacher_var, self.subject_var, self.class_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.periods_var, self.double_var = tk.StringVar(value="5"), tk.BooleanVar()
        with _connect() as conn:
            self.teachers = {row["name"]: row["id"] for row in list_teachers(conn, True)}; self.subjects = {f"{row['code']} — {row['name']}": row["id"] for row in list_subjects(conn, True)}; self.classes = [row["name"] for row in timetable_classes(conn)]
        form = ttk.Frame(self.window, padding=12); form.pack(fill="x")
        for column, (label, variable, values) in enumerate((("Teacher", self.teacher_var, list(self.teachers)), ("Subject", self.subject_var, list(self.subjects)), ("Class", self.class_var, self.classes))):
            ttk.Label(form, text=label).grid(row=0, column=column); ttk.Combobox(form, textvariable=variable, values=values, state="readonly", width=28).grid(row=1, column=column, padx=4)
        ttk.Button(form, text="Add Eligible Teacher", command=self.add_assignment).grid(row=1, column=3, padx=8)
        ttk.Label(form, text="Periods/week").grid(row=2, column=0, pady=(12, 0)); ttk.Entry(form, textvariable=self.periods_var, width=10).grid(row=3, column=0)
        ttk.Checkbutton(form, text="Double period allowed", variable=self.double_var).grid(row=3, column=1); ttk.Button(form, text="Save Requirement", command=self.add_requirement).grid(row=3, column=2)
        pane = ttk.Panedwindow(self.window, orient="horizontal"); pane.pack(fill="both", expand=True, padx=12, pady=8)
        self.assignment_tree = ttk.Treeview(pane, columns=("class", "subject", "teacher"), show="headings"); self.requirement_tree = ttk.Treeview(pane, columns=("class", "subject", "periods", "double"), show="headings")
        for tree, columns in ((self.assignment_tree, (("class", "Class"), ("subject", "Subject"), ("teacher", "Teacher"))), (self.requirement_tree, (("class", "Class"), ("subject", "Subject"), ("periods", "Periods/week"), ("double", "Double")))):
            for column, text in columns: tree.heading(column, text=text); tree.column(column, width=135)
            pane.add(tree, weight=1)
        buttons = ttk.Frame(self.window); buttons.pack(pady=8); ttk.Button(buttons, text="Delete Eligible Teacher", command=self.remove_assignment).pack(side="left", padx=6); ttk.Button(buttons, text="Delete Requirement", command=self.remove_requirement).pack(side="left", padx=6); self.refresh()

    def add_assignment(self):
        try:
            with _connect() as conn: save_assignment(conn, self.teachers[self.teacher_var.get()], self.subjects[self.subject_var.get()], self.class_var.get()); conn.commit()
            self.refresh()
        except Exception as exc: messagebox.showerror("Assignments", str(exc), parent=self.window)

    def add_requirement(self):
        try:
            subject_id = self.subjects[self.subject_var.get()]
            with _connect() as conn: save_requirement(conn, subject_id, self.class_var.get(), int(self.periods_var.get()), self.double_var.get()); conn.commit()
            self.refresh()
        except Exception as exc: messagebox.showerror("Requirements", str(exc), parent=self.window)

    def refresh(self):
        self.assignment_tree.delete(*self.assignment_tree.get_children()); self.requirement_tree.delete(*self.requirement_tree.get_children())
        with _connect() as conn:
            for row in list_assignments(conn): self.assignment_tree.insert("", "end", iid=f"{row['teacher_id']}:{row['subject_id']}:{row['class_name']}", values=(row["class_name"], row["subject_name"], row["teacher_name"]))
            for row in list_requirements(conn): self.requirement_tree.insert("", "end", iid=f"{row['subject_id']}:{row['class_name']}", values=(row["class_name"], row["subject_name"], row["periods_per_week"], "Yes" if row["double_period_allowed"] else "No"))

    def remove_assignment(self):
        selected = self.assignment_tree.selection()
        if not selected: return
        teacher, subject, class_name = selected[0].split(":", 2)
        with _connect() as conn: delete_assignment(conn, int(teacher), int(subject), class_name); conn.commit()
        self.refresh()

    def remove_requirement(self):
        selected = self.requirement_tree.selection()
        if not selected: return
        subject, class_name = selected[0].split(":", 1)
        with _connect() as conn: delete_requirement(conn, int(subject), class_name); conn.commit()
        self.refresh()


class CellEditDialog:
    def __init__(self, master, version_id: int, class_name: str, day: str, period_no: int, on_saved):
        self.window = _themed_toplevel(master, "Edit Timetable Cell", "470x260"); self.window.transient(master); self.window.grab_set()
        self.version_id, self.class_name, self.day, self.period_no, self.on_saved = version_id, class_name, day, period_no, on_saved
        self.subject_var, self.teacher_var = tk.StringVar(), tk.StringVar(); self.subjects = {}; self.teachers = {}
        with _connect() as conn:
            for row in conn.execute("""SELECT DISTINCT s.id,s.code,s.name FROM tt_assignments a JOIN tt_subjects s ON s.id=a.subject_id WHERE a.class_name=? AND s.is_active=1 ORDER BY s.name""", (class_name,)):
                self.subjects[f"{row['code']} — {row['name']}"] = row["id"]
        frame = ttk.Frame(self.window, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{class_name} | {day} | Period {period_no}").grid(row=0, column=0, columnspan=2, pady=8)
        ttk.Label(frame, text="Subject").grid(row=1, column=0, sticky="w"); self.subject_combo = ttk.Combobox(frame, textvariable=self.subject_var, values=("FREE",) + tuple(self.subjects), state="readonly", width=32); self.subject_combo.grid(row=1, column=1); self.subject_combo.bind("<<ComboboxSelected>>", self.load_teachers)
        ttk.Label(frame, text="Teacher").grid(row=2, column=0, sticky="w", pady=8); self.teacher_combo = ttk.Combobox(frame, textvariable=self.teacher_var, state="readonly", width=32); self.teacher_combo.grid(row=2, column=1)
        ttk.Button(frame, text="Save and Lock", command=self.save).grid(row=3, column=0, columnspan=2, pady=16)

    def load_teachers(self, _event=None):
        self.teachers = {}; subject_id = self.subjects.get(self.subject_var.get())
        if subject_id:
            with _connect() as conn:
                for row in conn.execute("""SELECT t.id,t.name FROM tt_assignments a JOIN tt_teachers t ON t.id=a.teacher_id WHERE a.class_name=? AND a.subject_id=? AND t.is_active=1 ORDER BY t.name""", (self.class_name, subject_id)): self.teachers[row["name"]] = row["id"]
        self.teacher_combo.configure(values=tuple(self.teachers)); self.teacher_var.set("")

    def save(self):
        try:
            if self.subject_var.get() == "FREE": subject_id = teacher_id = None
            else: subject_id = self.subjects[self.subject_var.get()]; teacher_id = self.teachers[self.teacher_var.get()]
            with _connect() as conn: save_timetable_cell(conn, self.version_id, self.class_name, self.day, self.period_no, subject_id, teacher_id, True); conn.commit()
            self.window.destroy(); self.on_saved()
        except Exception as exc: messagebox.showerror("Timetable Cell", str(exc), parent=self.window)


class TimetableWindow(WorkspacePage):
    """Permission-aware timetable workspace with setup, generation, views and exports."""

    @auth.require_permission("view_timetable")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        target = self._standalone_window or self
        self.language, self.ui_font = apply_theme(target)
        self.title("Automatic Timetable Generator"); self.geometry("1250x760"); self.configure(bg=SPLASH_BG)
        self.last_violations: list[str] = []; self.version_map = {}; self.class_cells = {}; self.teacher_cells = {}; self._solve_queue: Queue = Queue(); self.selected_cell = None
        ttk.Label(self, text="Automatic Timetable Generator", font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=18, pady=(14, 5))
        self.notebook = ttk.Notebook(self); self.notebook.pack(fill="both", expand=True, padx=14, pady=10)
        self.setup_tab = ttk.Frame(self.notebook, padding=16); self.generate_tab = ttk.Frame(self.notebook, padding=16); self.view_tab = ttk.Frame(self.notebook, padding=12); self.teacher_tab = ttk.Frame(self.notebook, padding=12); self.export_tab = ttk.Frame(self.notebook, padding=16); self.conflicts_tab = ttk.Frame(self.notebook, padding=12)
        for frame, title in ((self.setup_tab, "Setup"), (self.generate_tab, "Generate"), (self.view_tab, "View / Edit"), (self.teacher_tab, "Teacher View"), (self.export_tab, "Export"), (self.conflicts_tab, "Conflicts")): self.notebook.add(frame, text=title)
        self._build_setup(); self._build_generate(); self._build_view(); self._build_teacher(); self._build_export(); self._build_conflicts(); self.refresh_versions()

    def denied(self, frame, permission):
        ttk.Label(frame, text=f"You do not have the '{permission}' permission.", state="disabled").pack(pady=50); return True

    def _build_setup(self):
        if not auth.has_permission("manage_timetable"): return self.denied(self.setup_tab, "manage_timetable")
        ttk.Label(self.setup_tab, text="Configure the data used by the automatic solver.", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 14))
        for text, command in (("Subjects", lambda: SubjectWindow(self)), ("Teachers, Availability & Constraints", lambda: TeacherWindow(self)), ("Schedule Configuration", lambda: ScheduleConfigWindow(self)), ("Assignments & Weekly Requirements", lambda: AssignmentWindow(self))): ttk.Button(self.setup_tab, text=text, command=command).pack(fill="x", pady=6)

    def _build_generate(self):
        if not auth.has_permission("generate_timetable"): return self.denied(self.generate_tab, "generate_timetable")
        top = ttk.Frame(self.generate_tab); top.pack(fill="x")
        self.version_label = tk.StringVar(); ttk.Label(top, text="Version label").pack(side="left"); ttk.Entry(top, textvariable=self.version_label, width=30).pack(side="left", padx=8); self.generate_button = ttk.Button(top, text="Generate", command=self.generate); self.generate_button.pack(side="left")
        self.generation_status = tk.StringVar(value="Ready."); ttk.Label(self.generate_tab, textvariable=self.generation_status).pack(anchor="w", pady=10)
        self.version_tree = ttk.Treeview(self.generate_tab, columns=("label", "year", "generated", "published"), show="headings", height=13)
        for column, text, width in (("label", "Version", 260), ("year", "Academic Year", 140), ("generated", "Generated At", 180), ("published", "Published", 90)): self.version_tree.heading(column, text=text); self.version_tree.column(column, width=width)
        self.version_tree.pack(fill="both", expand=True); ttk.Button(self.generate_tab, text="Publish Selected Version", command=self.publish).pack(pady=8)

    def _version_controls(self, frame, include_class=False, include_teacher=False):
        controls = ttk.Frame(frame); controls.pack(fill="x", pady=(0, 8)); ttk.Label(controls, text="Version").pack(side="left")
        combo = ttk.Combobox(controls, state="readonly", width=32); combo.pack(side="left", padx=6)
        class_combo = teacher_combo = None
        if include_class: ttk.Label(controls, text="Class").pack(side="left", padx=(12, 0)); class_combo = ttk.Combobox(controls, state="readonly", width=20); class_combo.pack(side="left", padx=6)
        if include_teacher: ttk.Label(controls, text="Teacher").pack(side="left", padx=(12, 0)); teacher_combo = ttk.Combobox(controls, state="readonly", width=24); teacher_combo.pack(side="left", padx=6)
        return combo, class_combo, teacher_combo

    def _build_view(self):
        if not auth.has_permission("view_timetable"): return self.denied(self.view_tab, "view_timetable")
        self.view_version, self.class_combo, _ = self._version_controls(self.view_tab, include_class=True); self.view_version.bind("<<ComboboxSelected>>", lambda _e: self.load_classes()); self.class_combo.bind("<<ComboboxSelected>>", lambda _e: self.render_class_grid())
        self.class_grid = ttk.Frame(self.view_tab); self.class_grid.pack(fill="both", expand=True)

    def _build_teacher(self):
        if not auth.has_permission("view_timetable"): return self.denied(self.teacher_tab, "view_timetable")
        self.teacher_version, _, self.teacher_combo = self._version_controls(self.teacher_tab, include_teacher=True); self.teacher_combo.bind("<<ComboboxSelected>>", lambda _e: self.render_teacher_grid()); self.teacher_version.bind("<<ComboboxSelected>>", lambda _e: self.render_teacher_grid())
        self.teacher_grid = ttk.Frame(self.teacher_tab); self.teacher_grid.pack(fill="both", expand=True)

    def _build_export(self):
        if not auth.has_permission("view_timetable"): return self.denied(self.export_tab, "view_timetable")
        self.export_version, self.export_class, self.export_teacher = self._version_controls(self.export_tab, include_class=True, include_teacher=True)
        for text, function in (("Class Timetable PDF", self.export_class_pdf), ("Master Timetable PDF", self.export_master_pdf), ("Teacher Duty PDF", self.export_teacher_pdf), ("Timetable Excel", self.export_excel)): ttk.Button(self.export_tab, text=text, command=function).pack(fill="x", pady=7)

    def _build_conflicts(self):
        if not auth.has_permission("view_timetable"): return self.denied(self.conflicts_tab, "view_timetable")
        self.conflict_tree = ttk.Treeview(self.conflicts_tab, columns=("message",), show="headings"); self.conflict_tree.heading("message", text="Solver messages and soft-constraint conflicts"); self.conflict_tree.column("message", width=1000); self.conflict_tree.pack(fill="both", expand=True)

    def refresh_versions(self):
        with _connect() as conn:
            versions = list_versions(conn); classes = [row["name"] for row in timetable_classes(conn)]; teachers = list_teachers(conn, True)
        self.version_map = {f"#{row['id']} — {row['label']}": row["id"] for row in versions}; values = tuple(self.version_map)
        for name in ("view_version", "teacher_version", "export_version"):
            combo = getattr(self, name, None)
            if combo is not None: combo.configure(values=values); combo.set(values[0] if values else "")
        for combo in (getattr(self, "class_combo", None), getattr(self, "export_class", None)):
            if combo is not None: combo.configure(values=classes); combo.set(classes[0] if classes else "")
        self.teacher_map = {row["name"]: row["id"] for row in teachers}
        for combo in (getattr(self, "teacher_combo", None), getattr(self, "export_teacher", None)):
            if combo is not None: combo.configure(values=tuple(self.teacher_map)); combo.set(next(iter(self.teacher_map), ""))
        if hasattr(self, "version_tree"):
            self.version_tree.delete(*self.version_tree.get_children())
            for row in versions: self.version_tree.insert("", "end", iid=str(row["id"]), values=(row["label"], row["academic_year"], row["generated_at"], "Yes" if row["is_published"] else "No"))
        self.load_classes(); self.render_teacher_grid()

    def selected_version(self, combo) -> int:
        value = combo.get();
        if value not in self.version_map: raise ValueError("Select a timetable version.")
        return self.version_map[value]

    @auth.require_permission("generate_timetable")
    def generate(self):
        auth.touch_session(); label = self.version_label.get().strip()
        if not label: messagebox.showerror("Timetable", "Version label is required.", parent=self); return
        try:
            with _connect() as conn: problem = build_problem(conn)
        except Exception as exc: messagebox.showerror("Timetable", str(exc), parent=self); return
        self.generate_button.configure(state="disabled"); self.generation_status.set("Generating timetable…")
        threading.Thread(target=lambda: self._solve_queue.put(self._solve_with_fallbacks(problem)), daemon=True).start(); self.after(200, self.poll_generation)

    def _solve_with_fallbacks(self, problem):
        """Try strict generation, then safe relaxations so normal schools get a usable timetable."""
        attempts = [
            ({}, "strict constraints"),
            ({"strict_class_teacher_period_one": False}, "relaxed class-teacher period 1"),
            ({"strict_class_teacher_period_one": False, "relax_teacher_free_periods": True}, "relaxed class-teacher period 1 and minimum free periods"),
        ]
        first_failure = None
        for overrides, label in attempts:
            candidate = dict(problem)
            candidate.update(overrides)
            result = solve(candidate)
            if result.success:
                if label != "strict constraints":
                    result.violations.insert(0, f"Generated using {label}; review the Conflicts tab before publishing.")
                return result
            if first_failure is None:
                first_failure = result
        return first_failure

    def poll_generation(self):
        try: result = self._solve_queue.get_nowait()
        except Empty: self.after(200, self.poll_generation); return
        self.generate_button.configure(state="normal"); self.last_violations = result.violations; self.refresh_conflicts()
        if not result.success:
            self.generation_status.set(f"Generation failed after {result.stats['backtracks']} backtracks."); messagebox.showerror("Timetable", "\n".join(result.violations), parent=self); return
        try:
            with _connect() as conn:
                year_row = conn.execute("SELECT label FROM academic_years WHERE is_active=1 LIMIT 1").fetchone(); year = year_row[0] if year_row else "Unspecified"
                version_id = create_version(conn, self.version_label.get(), year); save_timetable_slots(conn, version_id, result.slots); conn.commit()
            self.generation_status.set(f"Generated {len(result.slots)} slots in {result.stats['duration_ms']} ms with {result.stats['backtracks']} backtracks."); self.refresh_versions()
        except Exception as exc: messagebox.showerror("Timetable", str(exc), parent=self)

    def refresh_conflicts(self):
        if not hasattr(self, "conflict_tree"): return
        self.conflict_tree.delete(*self.conflict_tree.get_children())
        for index, message in enumerate(self.last_violations): self.conflict_tree.insert("", "end", iid=str(index), values=(message,))

    @auth.require_permission("generate_timetable")
    def publish(self):
        selected = self.version_tree.selection()
        if not selected: return
        try:
            with _connect() as conn: publish_version(conn, int(selected[0])); conn.commit()
            self.refresh_versions()
        except Exception as exc: messagebox.showerror("Timetable", str(exc), parent=self)

    def load_classes(self):
        if not hasattr(self, "view_version"): return
        try:
            version_id = self.selected_version(self.view_version)
            with _connect() as conn:
                included = {row["name"] for row in timetable_classes(conn)}
                classes = [row[0] for row in conn.execute("SELECT DISTINCT class_name FROM tt_timetable WHERE version_id=? ORDER BY class_name", (version_id,)) if row[0] in included]
            self.class_combo.configure(values=classes); self.class_combo.set(classes[0] if classes else ""); self.render_class_grid()
        except ValueError: pass

    def render_grid(self, frame, slots, cell_command=None):
        for child in frame.winfo_children(): child.destroy()
        with _connect() as conn: config = get_schedule_config(conn)
        days = [day for day in config["working_days"].split(",") if day]; times = period_times(config); lookup = {(row["day"], int(row["period_no"])): row for row in slots}; subject_colors = {}
        ttk.Label(frame, text="Period").grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        for column, day in enumerate(days, 1): ttk.Label(frame, text=day, anchor="center").grid(row=0, column=column, sticky="nsew", padx=1, pady=1)
        for row_index, (start, end) in enumerate(times, 1):
            ttk.Label(frame, text=f"P{row_index}\n{start}-{end}", anchor="center").grid(row=row_index, column=0, sticky="nsew", padx=1, pady=1)
            for column, day in enumerate(days, 1):
                slot = lookup.get((day, row_index), {}); subject_id = slot.get("subject_id"); color = "#f3f3f3" if not subject_id else subject_colors.setdefault(subject_id, PASTELS[len(subject_colors) % len(PASTELS)])
                text = "FREE" if not subject_id else f"{slot.get('subject_code') or slot.get('subject_name')}\n{slot.get('teacher_name') or slot.get('class_name', '')}"
                label = tk.Label(frame, text=text, bg=color, relief="solid", bd=1, width=15, height=3, cursor="hand2" if cell_command else "arrow")
                label.grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                if cell_command: label.bind("<Button-1>", lambda _e, d=day, p=row_index: cell_command(d, p))
        for column in range(len(days) + 1): frame.columnconfigure(column, weight=1)

    def render_class_grid(self):
        if not hasattr(self, "class_grid") or not self.class_combo.get(): return
        try: version_id = self.selected_version(self.view_version)
        except ValueError: return
        with _connect() as conn: slots = list_timetable(conn, version_id, class_name=self.class_combo.get())
        self._class_slot_lookup = {(row["day"], int(row["period_no"])): row for row in slots}
        def edit_cell(day, period):
            self.selected_cell = (day, period)
            CellEditDialog(self, version_id, self.class_combo.get(), day, period, self.render_class_grid)
        self.render_grid(self.class_grid, slots, edit_cell)
        button = ttk.Button(self.class_grid, text="Lock / Unlock Last Selected Cell", command=self.toggle_selected_lock); button.grid(row=99, column=0, columnspan=3, pady=8)

    def toggle_selected_lock(self):
        if not self.selected_cell:
            messagebox.showwarning("Timetable", "Click a timetable cell first.", parent=self)
            return
        try:
            version_id = self.selected_version(self.view_version); day, period = self.selected_cell
            current = self._class_slot_lookup.get((day, period), {})
            with _connect() as conn:
                set_cell_lock(conn, version_id, self.class_combo.get(), day, period, not bool(current.get("is_locked")))
                conn.commit()
            self.render_class_grid()
        except Exception as exc:
            messagebox.showerror("Timetable", str(exc), parent=self)

    def render_teacher_grid(self):
        if not hasattr(self, "teacher_grid") or not self.teacher_combo.get(): return
        try: version_id = self.selected_version(self.teacher_version)
        except ValueError: return
        with _connect() as conn: slots = list_timetable(conn, version_id, teacher_id=self.teacher_map[self.teacher_combo.get()])
        self.render_grid(self.teacher_grid, slots)

    def export(self, function, *args):
        try:
            with _connect() as conn: path = function(conn, *args)
            if hasattr(os, "startfile"): os.startfile(path)
            messagebox.showinfo("Timetable Export", f"Saved to:\n{path}", parent=self)
        except Exception as exc: messagebox.showerror("Timetable Export", str(exc), parent=self)
    def export_class_pdf(self): self.export(class_timetable_pdf, self.selected_version(self.export_version), self.export_class.get())
    def export_master_pdf(self): self.export(master_timetable_pdf, self.selected_version(self.export_version))
    def export_teacher_pdf(self): self.export(teacher_duty_pdf, self.selected_version(self.export_version), self.teacher_map[self.export_teacher.get()])
    def export_excel(self): self.export(timetable_excel, self.selected_version(self.export_version))
