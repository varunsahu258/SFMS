"""Exam, paper, seating, and result management services for SFMS."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3


from config import REPORTS_DIR, SCHOOL_NAME
from utils import now_str

EXAM_TYPES = ("MONTHLY_TEST", "HALF_YEARLY", "PROJECT_WORK", "ANNUAL", "PERSONALITY", "OTHER")
GRADE_POINTS = ((90, "A+"), (75, "A"), (50, "B"), (33, "C"), (0, "D"))


def install_exam_schema(conn: sqlite3.Connection) -> None:
    """Install complete exam, paper, seating, and result tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS exams(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            exam_type TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            starts_on TEXT,
            ends_on TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(name,academic_year)
        );
        CREATE TABLE IF NOT EXISTS exam_subjects(
            id INTEGER PRIMARY KEY,
            exam_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            max_marks REAL NOT NULL DEFAULT 100,
            monthly_max REAL NOT NULL DEFAULT 10,
            half_yearly_max REAL NOT NULL DEFAULT 20,
            project_max REAL NOT NULL DEFAULT 10,
            annual_max REAL NOT NULL DEFAULT 60,
            exam_date TEXT,
            paper_status TEXT NOT NULL DEFAULT 'DRAFT',
            paper_file TEXT,
            stored_location TEXT,
            FOREIGN KEY(exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            UNIQUE(exam_id,class_name,subject_name)
        );
        CREATE TABLE IF NOT EXISTS exam_rooms(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            rows_count INTEGER NOT NULL,
            columns_count INTEGER NOT NULL,
            benches_per_cell INTEGER NOT NULL DEFAULT 1,
            capacity INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exam_seating_plans(
            id INTEGER PRIMARY KEY,
            exam_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(exam_id) REFERENCES exams(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS exam_seating_assignments(
            id INTEGER PRIMARY KEY,
            plan_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            row_no INTEGER NOT NULL,
            column_no INTEGER NOT NULL,
            bench_position INTEGER NOT NULL CHECK(bench_position IN (1,2)),
            student_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES exam_seating_plans(id) ON DELETE CASCADE,
            FOREIGN KEY(room_id) REFERENCES exam_rooms(id),
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(plan_id,room_id,row_no,column_no,bench_position),
            UNIQUE(plan_id,student_id)
        );
        CREATE TABLE IF NOT EXISTS exam_marks(
            id INTEGER PRIMARY KEY,
            exam_subject_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            monthly_marks REAL DEFAULT 0,
            half_yearly_marks REAL DEFAULT 0,
            project_marks REAL DEFAULT 0,
            annual_marks REAL DEFAULT 0,
            grade TEXT,
            remarks TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(exam_subject_id) REFERENCES exam_subjects(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(exam_subject_id,student_id)
        );
        CREATE TABLE IF NOT EXISTS exam_personality_grades(
            id INTEGER PRIMARY KEY,
            exam_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            indicator_name TEXT NOT NULL,
            term1_grade TEXT,
            term2_grade TEXT,
            FOREIGN KEY(exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(exam_id,student_id,category,indicator_name)
        );
        """
    )


def _rows(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(r) if hasattr(r, "keys") else dict(zip(cols, r)) for r in cursor.fetchall()]


def create_exam(conn, name: str, exam_type: str, academic_year: str, starts_on: str = "", ends_on: str = "") -> int:
    install_exam_schema(conn)
    exam_type = (exam_type or "OTHER").upper()
    if exam_type not in EXAM_TYPES:
        exam_type = "OTHER"
    cur = conn.execute(
        "INSERT INTO exams(name,exam_type,academic_year,starts_on,ends_on,created_at) VALUES(?,?,?,?,?,?)",
        (name.strip(), exam_type, academic_year.strip(), starts_on, ends_on, now_str()),
    )
    return int(cur.lastrowid)


def add_exam_subject(conn, exam_id: int, class_name: str, subject_name: str, max_marks: float = 100, exam_date: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO exam_subjects(exam_id,class_name,subject_name,max_marks,exam_date)
           VALUES(?,?,?,?,?) ON CONFLICT(exam_id,class_name,subject_name) DO UPDATE SET max_marks=excluded.max_marks,exam_date=excluded.exam_date""",
        (exam_id, class_name, subject_name, float(max_marks), exam_date),
    )
    return int(cur.lastrowid or conn.execute("SELECT id FROM exam_subjects WHERE exam_id=? AND class_name=? AND subject_name=?", (exam_id, class_name, subject_name)).fetchone()[0])


def upsert_room(conn, name: str, rows_count: int, columns_count: int, benches_per_cell: int = 1) -> int:
    capacity = int(rows_count) * int(columns_count) * 2 * int(benches_per_cell)
    cur = conn.execute(
        """INSERT INTO exam_rooms(name,rows_count,columns_count,benches_per_cell,capacity) VALUES(?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET rows_count=excluded.rows_count,columns_count=excluded.columns_count,benches_per_cell=excluded.benches_per_cell,capacity=excluded.capacity""",
        (name.strip(), rows_count, columns_count, benches_per_cell, capacity),
    )
    return int(cur.lastrowid or conn.execute("SELECT id FROM exam_rooms WHERE name=?", (name.strip(),)).fetchone()[0])


def generate_seating_plan(conn, exam_id: int, name: str, class_names: list[str], room_ids: list[int]) -> int:
    """Create two-per-bench seating: benchmates differ, vertical neighbours match class when possible."""
    plan_id = conn.execute("INSERT INTO exam_seating_plans(exam_id,name,created_at) VALUES(?,?,?)", (exam_id, name, now_str())).lastrowid
    students_by_class = {c: _rows(conn.execute("SELECT id,name,class FROM students WHERE class=? ORDER BY serial_no, name, id", (c,))) for c in class_names}
    for room in _rows(conn.execute(f"SELECT * FROM exam_rooms WHERE id IN ({','.join('?' for _ in room_ids)}) ORDER BY name", room_ids)):
        for col in range(1, int(room["columns_count"]) + 1):
            pair_classes = class_names[(col - 1) % len(class_names)], class_names[col % len(class_names)] if len(class_names) > 1 else class_names[0]
            for row in range(1, int(room["rows_count"]) + 1):
                for pos, cls in enumerate(pair_classes, 1):
                    if pos == 2 and cls == pair_classes[0] and len(class_names) == 1:
                        continue
                    pool = students_by_class.get(cls, [])
                    if not pool:
                        continue
                    student = pool.pop(0)
                    conn.execute("""INSERT INTO exam_seating_assignments(plan_id,room_id,row_no,column_no,bench_position,student_id,class_name)
                                    VALUES(?,?,?,?,?,?,?)""", (plan_id, room["id"], row, col, pos, student["id"], cls))
    return int(plan_id)


def save_marks(conn, exam_subject_id: int, student_id: int, monthly=0, half_yearly=0, project=0, annual=0, grade: str = "", remarks: str = "") -> None:
    total = sum(float(x or 0) for x in (monthly, half_yearly, project, annual))
    grade = grade or calculate_grade(total)
    conn.execute("""INSERT INTO exam_marks(exam_subject_id,student_id,monthly_marks,half_yearly_marks,project_marks,annual_marks,grade,remarks,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(exam_subject_id,student_id) DO UPDATE SET monthly_marks=excluded.monthly_marks,half_yearly_marks=excluded.half_yearly_marks,project_marks=excluded.project_marks,annual_marks=excluded.annual_marks,grade=excluded.grade,remarks=excluded.remarks,updated_at=excluded.updated_at""",
                 (exam_subject_id, student_id, monthly, half_yearly, project, annual, grade, remarks, now_str()))


def calculate_grade(percent: float) -> str:
    for threshold, grade in GRADE_POINTS:
        if float(percent) >= threshold:
            return grade
    return "D"


def _path(stem: str) -> str:
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    return str(Path(REPORTS_DIR) / f"{stem}_{datetime.now():%Y%m%d_%H%M%S}.pdf")


def marksheet_pdf(conn, exam_id: int, student_id: int) -> str:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

    styles = getSampleStyleSheet(); centered = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9)
    student = _rows(conn.execute("SELECT * FROM students WHERE id=?", (student_id,)))[0]
    exam = _rows(conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)))[0]
    rows = _rows(conn.execute("""SELECT es.*,m.* FROM exam_subjects es LEFT JOIN exam_marks m ON m.exam_subject_id=es.id AND m.student_id=?
                                WHERE es.exam_id=? AND es.class_name=? ORDER BY es.subject_name""", (student_id, exam_id, student.get("class"))))
    path = _path(f"marksheet_{student_id}_{exam_id}")
    story = [Paragraph(SCHOOL_NAME, ParagraphStyle("title", parent=styles["Title"], alignment=TA_CENTER, fontSize=16)), Paragraph(f"Report Card {exam['academic_year']}", centered), Spacer(1, 4*mm)]
    info = [["Student's Name", student.get("name", ""), "Class", student.get("class", "")], ["Father's Name", student.get("father_name", ""), "Roll No", student.get("roll_no", "") or student.get("serial_no", "")], ["Mother's Name", student.get("mother_name", ""), "Sch No.", student.get("scholar_no", "")], ["Address", student.get("address", ""), "DOB", student.get("dob", "")]]
    story.append(Table(info, colWidths=[32*mm, 78*mm, 25*mm, 35*mm], style=[("GRID",(0,0),(-1,-1),.5,colors.black), ("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold")]))
    data = [["Subjects","Max. Marks","Monthly Test (10)","Half Yearly (20)","Project Work (10)","Annual Exam (60)","Total Obt.","Grade"]]
    totals = [0,0,0,0,0,0]
    for r in rows:
        vals = [float(r.get(k) or 0) for k in ("max_marks","monthly_marks","half_yearly_marks","project_marks","annual_marks")]
        total = sum(vals[1:]); totals = [a+b for a,b in zip(totals, vals+[total])]
        data.append([r["subject_name"], *[f"{v:g}" for v in vals], f"{total:g}", r.get("grade") or calculate_grade(total)])
    data.append(["TOTAL", *[f"{v:g}" for v in totals]])
    story.append(Spacer(1, 4*mm)); story.append(Table(data, repeatRows=1, style=[("GRID",(0,0),(-1,-1),.5,colors.black), ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("ALIGN",(1,1),(-1,-1),"CENTER")]))
    obtained, maximum = totals[-1], totals[0] or 1
    story += [Spacer(1, 8*mm), Table([["Class Teacher's Remark", "GOOD"], ["Percentage", f"{obtained/maximum*100:.2f}%"], ["Attendance", ""]], colWidths=[85*mm,85*mm], style=[("GRID",(0,0),(-1,-1),.5,colors.black), ("ALIGN",(1,0),(1,-1),"CENTER")]), Spacer(1, 30*mm), Table([["Class Teacher Signature", "Principal Signature"]], colWidths=[85*mm,85*mm], style=[("ALIGN",(0,0),(-1,-1),"CENTER")])]
    SimpleDocTemplate(path, pagesize=A4).build(story); return path


def result_diary_pdf(conn, exam_id: int, class_name: str) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table
    from reportlab.lib.styles import getSampleStyleSheet

    path = _path(f"result_diary_{exam_id}_{class_name.replace(' ','_')}")
    data = [["Roll", "Student", "Total", "%", "Grade", "PTM Remark"]]
    subjects = _rows(conn.execute("SELECT id,max_marks FROM exam_subjects WHERE exam_id=? AND class_name=?", (exam_id, class_name)))
    max_total = sum(float(s["max_marks"] or 0) for s in subjects) or 1
    for st in _rows(conn.execute("SELECT * FROM students WHERE class=? ORDER BY serial_no,name", (class_name,))):
        total = conn.execute(f"SELECT COALESCE(SUM(monthly_marks+half_yearly_marks+project_marks+annual_marks),0) FROM exam_marks WHERE student_id=? AND exam_subject_id IN ({','.join('?' for _ in subjects)})", [st["id"], *[s["id"] for s in subjects]]).fetchone()[0] if subjects else 0
        pct = total / max_total * 100
        data.append([st.get("roll_no","") or st.get("serial_no",""), st.get("name",""), f"{total:g}", f"{pct:.2f}%", calculate_grade(pct), ""])
    story = [Paragraph(f"{SCHOOL_NAME} - Result Diary / PTM", getSampleStyleSheet()["Title"]), Table(data, repeatRows=1, style=[("GRID",(0,0),(-1,-1),.5,colors.black), ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold")])]
    SimpleDocTemplate(path, pagesize=landscape(A4)).build(story); return path
