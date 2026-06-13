"""Class and section master-data management for SFMS."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import auth
from ui_workspace import WorkspacePage
from config import SPLASH_BG, SPLASH_FG
from ui_master_utils import audit, connect_db, ensure_permission_write
from utils import now_str


class ClassSectionWindow(WorkspacePage):
    """Manage class and section values as an administrator or accountant."""

    @auth.require_permission("manage_classes")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Classes and Sections")
        self.geometry("760x520")
        self.configure(bg=SPLASH_BG)
        self.class_var = tk.StringVar()
        self.section_var = tk.StringVar()
        self.in_timetable_var = tk.BooleanVar(value=True)
        self.class_teacher_var = tk.StringVar()
        self.teacher_map: dict[str, int | None] = {"None": None}
        self._build_widgets()
        self.refresh()

    def _build_widgets(self) -> None:
        heading = tk.Label(
            self, text="Class and Section Master", bg=SPLASH_BG, fg=SPLASH_FG,
            font=("Segoe UI", 18, "bold"),
        )
        heading.pack(pady=(18, 10))
        form = tk.Frame(self, bg=SPLASH_BG)
        form.pack(fill="x", padx=18, pady=8)
        tk.Label(form, text="Class", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.class_var, width=25).grid(row=0, column=1, padx=8)
        ttk.Button(form, text="Add Class", command=self.add_class).grid(row=0, column=2, padx=4)
        ttk.Checkbutton(form, text="Include in timetable", variable=self.in_timetable_var).grid(row=0, column=3, padx=8, sticky="w")
        tk.Label(form, text="Class teacher", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=3, sticky="w")
        self.teacher_combo = ttk.Combobox(form, textvariable=self.class_teacher_var, state="readonly", width=24)
        self.teacher_combo.grid(row=1, column=4, padx=8, pady=10)
        tk.Label(form, text="Section", bg=SPLASH_BG, fg=SPLASH_FG).grid(row=1, column=0, sticky="w", pady=10)
        ttk.Entry(form, textvariable=self.section_var, width=25).grid(row=1, column=1, padx=8, pady=10)
        ttk.Button(form, text="Add Section to Selected Class", command=self.add_section).grid(row=1, column=2, padx=4)

        columns = ("class", "section", "status", "timetable", "teacher")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        for column, heading_text, width in (
            ("class", "Class", 220), ("section", "Section", 160), ("status", "Status", 90),
            ("timetable", "Timetable", 90), ("teacher", "Class Teacher", 150),
        ):
            self.tree.heading(column, text=heading_text)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=18, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        controls = tk.Frame(self, bg=SPLASH_BG)
        controls.pack(fill="x", padx=18, pady=(0, 16))
        ttk.Button(controls, text="Deactivate Selected Section", command=self.deactivate_selected).pack(side="left")
        ttk.Button(controls, text="Deactivate Class", command=self.deactivate_class).pack(side="left", padx=8)
        ttk.Button(controls, text="Save Timetable Settings", command=self.save_timetable_settings).pack(side="left", padx=8)
        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side="left")

    def refresh(self) -> None:
        auth.touch_session()
        self.tree.delete(*self.tree.get_children())
        with connect_db() as conn:
            class_columns = {row[1] for row in conn.execute("PRAGMA table_info(classes)")}
            if "is_in_timetable" not in class_columns:
                conn.execute("ALTER TABLE classes ADD COLUMN is_in_timetable INTEGER NOT NULL DEFAULT 1")
            if "class_teacher_id" not in class_columns:
                conn.execute("ALTER TABLE classes ADD COLUMN class_teacher_id INTEGER REFERENCES tt_teachers(id)")
            self.teacher_map = {"None": None}
            try:
                for teacher in conn.execute("SELECT id,name FROM tt_teachers WHERE is_active=1 ORDER BY name"):
                    self.teacher_map[teacher["name"]] = teacher["id"]
            except Exception:
                pass
            rows = conn.execute(
                """SELECT c.id AS class_id,c.name AS class_name,c.is_active AS class_active,
                          c.is_in_timetable,c.class_teacher_id,t.name AS class_teacher_name,
                          s.id AS section_id,s.name AS section_name,s.is_active AS section_active
                   FROM classes c LEFT JOIN sections s ON s.class_id=c.id
                   LEFT JOIN tt_teachers t ON t.id=c.class_teacher_id
                   ORDER BY c.name,s.name"""
            ).fetchall()
            conn.commit()
        self.teacher_combo.configure(values=tuple(self.teacher_map))
        if not self.class_teacher_var.get():
            self.class_teacher_var.set("None")
        for row in rows:
            active = bool(row["class_active"]) and (row["section_id"] is None or bool(row["section_active"]))
            iid = f"{row['class_id']}:{row['section_id'] or 0}"
            self.tree.insert("", "end", iid=iid, values=(
                row["class_name"], row["section_name"] or "", "Active" if active else "Inactive",
                "Yes" if row["is_in_timetable"] else "No", row["class_teacher_name"] or "",
            ))

    def _selection_changed(self, _event=None) -> None:
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0], "values")
            self.class_var.set(values[0])
            self.section_var.set(values[1])
            self.in_timetable_var.set(values[3] == "Yes")
            self.class_teacher_var.set(values[4] or "None")

    @auth.require_permission("manage_classes")
    def add_class(self) -> None:
        if not ensure_permission_write("manage_classes"):
            return
        name = self.class_var.get().strip()
        if not name:
            messagebox.showerror("Classes", "Class name is required.", parent=self)
            return
        with connect_db() as conn:
            existing = conn.execute("SELECT id,is_active FROM classes WHERE name=?", (name,)).fetchone()
            if existing:
                conn.execute("UPDATE classes SET is_active=1,is_in_timetable=?,class_teacher_id=? WHERE id=?", (int(self.in_timetable_var.get()), self.teacher_map.get(self.class_teacher_var.get()), existing["id"]))
                class_id = existing["id"]
                action = "CLASS_REACTIVATE"
            else:
                cursor = conn.execute("INSERT INTO classes(name,is_active,created_at,is_in_timetable,class_teacher_id) VALUES(?,1,?,?,?)", (name, now_str(), int(self.in_timetable_var.get()), self.teacher_map.get(self.class_teacher_var.get())))
                class_id = cursor.lastrowid
                action = "CLASS_ADD"
            audit(conn, action, "classes", class_id, None, {"name": name, "is_active": 1, "is_in_timetable": int(self.in_timetable_var.get()), "class_teacher_id": self.teacher_map.get(self.class_teacher_var.get())})
        self.section_var.set("")
        self.refresh()

    @auth.require_permission("manage_classes")
    def save_timetable_settings(self) -> None:
        if not ensure_permission_write("manage_classes"):
            return
        class_name = self.class_var.get().strip()
        if not class_name:
            messagebox.showwarning("Classes", "Select or enter a class.", parent=self)
            return
        with connect_db() as conn:
            row = conn.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()
            if row is None:
                messagebox.showerror("Classes", "Class was not found.", parent=self)
                return
            payload = {"class": class_name, "is_in_timetable": int(self.in_timetable_var.get()), "class_teacher_id": self.teacher_map.get(self.class_teacher_var.get())}
            conn.execute("UPDATE classes SET is_in_timetable=?,class_teacher_id=? WHERE id=?", (payload["is_in_timetable"], payload["class_teacher_id"], row["id"]))
            audit(conn, "CLASS_TIMETABLE_SETTINGS", "classes", row["id"], None, payload)
        self.refresh()

    @auth.require_permission("manage_classes")
    def add_section(self) -> None:
        if not ensure_permission_write("manage_classes"):
            return
        class_name = self.class_var.get().strip()
        section_name = self.section_var.get().strip()
        if not class_name or not section_name:
            messagebox.showerror("Sections", "Select a class and enter a section name.", parent=self)
            return
        with connect_db() as conn:
            class_row = conn.execute("SELECT id FROM classes WHERE name=? AND is_active=1", (class_name,)).fetchone()
            if class_row is None:
                messagebox.showerror("Sections", "Add or reactivate the class first.", parent=self)
                return
            existing = conn.execute("SELECT id FROM sections WHERE class_id=? AND name=?", (class_row["id"], section_name)).fetchone()
            if existing:
                conn.execute("UPDATE sections SET is_active=1 WHERE id=?", (existing["id"],))
                section_id = existing["id"]
                action = "SECTION_REACTIVATE"
            else:
                cursor = conn.execute(
                    "INSERT INTO sections(class_id,name,is_active,created_at) VALUES(?,?,1,?)",
                    (class_row["id"], section_name, now_str()),
                )
                section_id = cursor.lastrowid
                action = "SECTION_ADD"
            audit(conn, action, "sections", section_id, None, {"class": class_name, "name": section_name, "is_active": 1})
        self.section_var.set("")
        self.refresh()

    @auth.require_permission("manage_classes")
    def deactivate_class(self) -> None:
        if not ensure_permission_write("manage_classes"):
            return
        class_name = self.class_var.get().strip()
        if not class_name:
            messagebox.showwarning("Classes", "Select or enter a class.", parent=self)
            return
        with connect_db() as conn:
            row = conn.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()
            if row is None:
                messagebox.showerror("Classes", "Class was not found.", parent=self)
                return
            conn.execute("UPDATE classes SET is_active=0 WHERE id=?", (row["id"],))
            conn.execute("UPDATE sections SET is_active=0 WHERE class_id=?", (row["id"],))
            audit(conn, "CLASS_DEACTIVATE", "classes", row["id"], None, {"class": class_name})
        self.refresh()

    @auth.require_permission("manage_classes")
    def deactivate_selected(self) -> None:
        if not ensure_permission_write("manage_classes"):
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Classes", "Select a class or section.", parent=self)
            return
        class_id, section_id = (int(value) for value in selected[0].split(":"))
        values = self.tree.item(selected[0], "values")
        with connect_db() as conn:
            if section_id:
                conn.execute("UPDATE sections SET is_active=0 WHERE id=?", (section_id,))
                audit(conn, "SECTION_DEACTIVATE", "sections", section_id, None, {"class": values[0], "section": values[1]})
            else:
                conn.execute("UPDATE classes SET is_active=0 WHERE id=?", (class_id,))
                conn.execute("UPDATE sections SET is_active=0 WHERE class_id=?", (class_id,))
                audit(conn, "CLASS_DEACTIVATE", "classes", class_id, None, {"class": values[0]})
        self.refresh()
