"""Exam, seating, and result management coverage."""

from __future__ import annotations

import sqlite3

from exam_service import (
    add_exam_subject,
    calculate_grade,
    create_exam,
    generate_seating_plan,
    install_exam_schema,
    marksheet_pdf,
    result_diary_pdf,
    save_marks,
    save_personality_grade,
    update_paper,
    upsert_room,
)
from migrations import migration_v019_exam_result_management


def exam_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE students(
            id INTEGER PRIMARY KEY,
            scholar_no TEXT,
            serial_no TEXT,
            name TEXT NOT NULL,
            father_name TEXT,
            mother_name TEXT,
            address TEXT,
            dob TEXT,
            class TEXT
        );
        INSERT INTO students(id,scholar_no,serial_no,name,class) VALUES
            (1,'701','1','Ayushi Sahu','7th'),
            (2,'801','1','Ravi Sharma','8th'),
            (3,'702','2','Neha Patel','7th'),
            (4,'802','2','Aman Jain','8th');
        """
    )
    migration_v019_exam_result_management(conn)
    return conn


def test_v019_creates_complete_exam_result_tables():
    conn = exam_conn()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "exams", "exam_subjects", "exam_rooms", "exam_seating_plans",
        "exam_seating_assignments", "exam_marks", "exam_personality_grades",
    } <= tables


def test_exam_subject_room_seating_and_marks_workflow():
    conn = exam_conn()
    exam_id = create_exam(conn, "Annual Exams", "ANNUAL", "2025-26")
    subject_id = add_exam_subject(conn, exam_id, "7th", "English", 100)
    room_id = upsert_room(conn, "Classroom A", 2, 1)

    plan_id = generate_seating_plan(conn, exam_id, "Annual Main", ["7th", "8th"], [room_id])
    seats = [dict(row) for row in conn.execute("SELECT * FROM exam_seating_assignments WHERE plan_id=? ORDER BY row_no,bench_position", (plan_id,))]

    assert len(seats) == 4
    assert seats[0]["row_no"] == 1 and seats[2]["row_no"] == 2
    assert seats[0]["class_name"] == seats[2]["class_name"]
    assert seats[1]["class_name"] == seats[3]["class_name"]
    assert seats[0]["class_name"] != seats[1]["class_name"]

    save_marks(conn, subject_id, 1, monthly=7, half_yearly=9, project=10, annual=51)
    row = conn.execute("SELECT * FROM exam_marks WHERE exam_subject_id=? AND student_id=1", (subject_id,)).fetchone()
    assert row["grade"] == calculate_grade(77)


def test_paper_storage_personality_and_pdf_fallbacks(tmp_path, monkeypatch):
    import exam_service

    monkeypatch.setattr(exam_service, "REPORTS_DIR", tmp_path)
    conn = exam_conn()
    exam_id = create_exam(conn, "Annual Exams", "ANNUAL", "2025-26")
    subject_id = add_exam_subject(conn, exam_id, "7th", "English", 100)
    update_paper(conn, subject_id, "STORED", "/papers/english.pdf", "Steel Almirah A")
    save_marks(conn, subject_id, 1, monthly=7, half_yearly=9, project=10, annual=51)
    save_personality_grade(conn, exam_id, 1, "Personal Quality", "Hard Work", "A", "A")

    paper = conn.execute("SELECT * FROM exam_subjects WHERE id=?", (subject_id,)).fetchone()
    assert paper["paper_status"] == "STORED"
    assert paper["stored_location"] == "Steel Almirah A"
    assert marksheet_pdf(conn, exam_id, 1).endswith(".pdf")
    assert result_diary_pdf(conn, exam_id, "7th").endswith(".pdf")
