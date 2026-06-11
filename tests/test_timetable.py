"""Timetable migration, DB helper, solver, and navigation coverage."""

from __future__ import annotations

import sqlite3

from migrations import migration_v012_timetable
from timetable_db import build_problem, period_times, teacher_available_periods
from timetable_solver import solve


def timetable_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT UNIQUE,is_active INTEGER DEFAULT 1);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO users VALUES(1,'admin');
        INSERT INTO classes VALUES(1,'Class 1',1);
        """
    )
    migration_v012_timetable(conn)
    return conn


def test_v012_creates_timetable_tables_and_default_config():
    conn = timetable_conn()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "tt_subjects", "tt_teachers", "tt_teacher_availability", "tt_teacher_constraints",
        "tt_assignments", "tt_subject_requirements", "tt_schedule_config", "tt_versions", "tt_timetable",
    } <= tables
    config = dict(conn.execute("SELECT * FROM tt_schedule_config WHERE id=1").fetchone())
    assert config["periods_per_day"] == 8
    assert config["working_days"] == "MON,TUE,WED,THU,FRI,SAT"


def test_period_times_include_break_and_lunch_gaps():
    config = {
        "periods_per_day": 4, "period_duration_min": 40, "day_start_time": "08:00",
        "break_after_period": 1, "break_duration_min": 10,
        "lunch_after_period": 3, "lunch_duration_min": 30,
    }
    assert period_times(config) == [
        ("08:00", "08:40"), ("08:50", "09:30"),
        ("09:30", "10:10"), ("10:40", "11:20"),
    ]


def test_partial_availability_means_absent_on_missing_days_and_requires_full_period_window():
    conn = timetable_conn()
    conn.execute("INSERT INTO tt_teachers(name,max_periods_day,is_active,created_at) VALUES('A',6,1,'now')")
    teacher_id = conn.execute("SELECT id FROM tt_teachers").fetchone()[0]
    conn.execute("INSERT INTO tt_teacher_availability VALUES(?,?,?,?)", (teacher_id, "MON", "08:30", "10:00"))
    config = dict(conn.execute("SELECT * FROM tt_schedule_config WHERE id=1").fetchone())
    available = teacher_available_periods(conn, teacher_id, config)
    assert available["MON"] == [2]
    assert available["TUE"] == []


def test_build_problem_is_self_contained_and_solver_obeys_hard_constraints():
    conn = timetable_conn()
    conn.execute("UPDATE tt_schedule_config SET periods_per_day=2,working_days='MON,TUE',period_duration_min=40,day_start_time='08:00',break_after_period=NULL,break_duration_min=0,lunch_after_period=NULL,lunch_duration_min=0")
    conn.execute("INSERT INTO tt_subjects VALUES(1,'Maths','MAT',0,1,'now')")
    conn.execute("INSERT INTO tt_subjects VALUES(2,'English','ENG',0,1,'now')")
    conn.execute("INSERT INTO tt_teachers VALUES(1,'Teacher A','',1,1,'now')")
    conn.execute("INSERT INTO tt_teachers VALUES(2,'Teacher B','',2,1,'now')")
    conn.execute("INSERT INTO tt_assignments VALUES(1,1,'Class 1')")
    conn.execute("INSERT INTO tt_assignments VALUES(2,2,'Class 1')")
    conn.execute("INSERT INTO tt_subject_requirements VALUES(1,'Class 1',2,0)")
    conn.execute("INSERT INTO tt_subject_requirements VALUES(2,'Class 1',1,0)")
    conn.execute("INSERT INTO tt_teacher_constraints VALUES(1,'MON',1,'UNAVAILABLE')")

    problem = build_problem(conn)
    conn.close()
    result = solve(problem)

    assert result.success is True
    maths = [slot for slot in result.slots if slot["subject_id"] == 1]
    assert len(maths) == 2
    assert all(not (slot["day"] == "MON" and slot["period_no"] == 1) for slot in maths)
    assert len({(slot["class_name"], slot["day"], slot["period_no"]) for slot in result.slots}) == len(result.slots)
    assert result.stats["backtracks"] <= 50_000


def test_solver_returns_descriptive_failure_when_assignment_is_missing():
    problem = {
        "classes": ["Class 1"], "days": ["MON"], "periods": [1], "period_times": [("08:00", "08:40")],
        "subjects": {1: {"id": 1, "name": "Maths"}}, "teachers": {},
        "requirements": [{"class_name": "Class 1", "subject_id": 1, "periods_per_week": 1}],
        "assignments": {}, "availability": {}, "constraints": {},
    }
    result = solve(problem)
    assert result.success is False
    assert "No eligible teacher" in result.violations[0]


def test_timetable_exports_create_pdf_and_excel(tmp_path, monkeypatch):
    import timetable_report
    from timetable_db import create_version, save_timetable_slots

    monkeypatch.setattr(timetable_report, "REPORTS_DIR", tmp_path)
    conn = timetable_conn()
    conn.execute("INSERT INTO tt_subjects VALUES(1,'Maths','MAT',0,1,'now')")
    conn.execute("INSERT INTO tt_teachers VALUES(1,'Teacher A','',6,1,'now')")
    version_id = create_version(conn, "Test Version", "2026-27", 1)
    config = dict(conn.execute("SELECT * FROM tt_schedule_config WHERE id=1").fetchone())
    slots = []
    for day in config["working_days"].split(","):
        for period in range(1, config["periods_per_day"] + 1):
            slots.append({
                "class_name": "Class 1", "day": day, "period_no": period,
                "subject_id": 1 if day == "MON" and period == 1 else None,
                "teacher_id": 1 if day == "MON" and period == 1 else None,
                "is_free": not (day == "MON" and period == 1), "is_locked": 0,
            })
    save_timetable_slots(conn, version_id, slots)
    conn.commit()

    class_pdf = timetable_report.class_timetable_pdf(conn, version_id, "Class 1")
    master_pdf = timetable_report.master_timetable_pdf(conn, version_id)
    teacher_pdf = timetable_report.teacher_duty_pdf(conn, version_id, 1)
    workbook = timetable_report.timetable_excel(conn, version_id)

    for path in (class_pdf, master_pdf, teacher_pdf, workbook):
        assert __import__("pathlib").Path(path).is_file()
        assert __import__("pathlib").Path(path).stat().st_size > 0
