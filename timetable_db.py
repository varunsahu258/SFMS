"""Database access and problem construction for SFMS timetables."""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from typing import Iterable

import auth
from audit import log_action
from utils import now_str

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT")
CONSTRAINT_TYPES = ("UNAVAILABLE", "PREFERRED_FREE", "PREFERRED_TEACH")


def _rows(cursor) -> list[dict]:
    columns = [item[0] for item in cursor.description]
    return [dict(row) if hasattr(row, "keys") else dict(zip(columns, row)) for row in cursor.fetchall()]


def _row(cursor) -> dict | None:
    rows = _rows(cursor)
    return rows[0] if rows else None


def _user_id() -> int | None:
    return auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION is not None else None


def _audit(conn, action: str, table: str, record_id, old=None, new=None) -> None:
    log_action(conn, _user_id(), action, table, record_id, old, new)


def _require_text(value, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _time(value: str) -> datetime:
    try:
        return datetime.strptime(str(value), "%H:%M")
    except ValueError as exc:
        raise ValueError("Time must use HH:MM format.") from exc


def get_schedule_config(conn) -> dict:
    row = _row(conn.execute("SELECT * FROM tt_schedule_config WHERE id=1"))
    if row is None:
        raise ValueError("Timetable schedule configuration is missing.")
    return row


def save_schedule_config(conn, values: dict) -> None:
    old = get_schedule_config(conn)
    days = [day.strip().upper() for day in str(values.get("working_days", "")).split(",") if day.strip()]
    if not days or any(day not in DAYS for day in days):
        raise ValueError("Working days must contain MON–SAT values.")
    periods = int(values["periods_per_day"])
    duration = int(values["period_duration_min"])
    start = _require_text(values.get("day_start_time"), "Day start time")
    _time(start)
    break_after = values.get("break_after_period")
    lunch_after = values.get("lunch_after_period")
    break_after = int(break_after) if str(break_after or "").strip() else None
    lunch_after = int(lunch_after) if str(lunch_after or "").strip() else None
    if periods <= 0 or duration <= 0:
        raise ValueError("Periods and duration must be positive.")
    for position, label in ((break_after, "Break"), (lunch_after, "Lunch")):
        if position is not None and not 1 <= position < periods:
            raise ValueError(f"{label} position must be between period 1 and {periods - 1}.")
    payload = {
        "periods_per_day": periods, "working_days": ",".join(days),
        "period_duration_min": duration, "day_start_time": start,
        "break_after_period": break_after, "break_duration_min": int(values.get("break_duration_min") or 0),
        "lunch_after_period": lunch_after, "lunch_duration_min": int(values.get("lunch_duration_min") or 0),
    }
    conn.execute(
        """UPDATE tt_schedule_config SET periods_per_day=?,working_days=?,period_duration_min=?,
           day_start_time=?,break_after_period=?,break_duration_min=?,lunch_after_period=?,lunch_duration_min=? WHERE id=1""",
        tuple(payload.values()),
    )
    _audit(conn, "TIMETABLE_CONFIG_SAVE", "tt_schedule_config", 1, old, payload)


def period_times(config: dict) -> list[tuple[str, str]]:
    """Derive period start/end times, inserting break and lunch gaps."""
    count = int(config["periods_per_day"])
    duration = int(config["period_duration_min"])
    current = _time(config["day_start_time"])
    result = []
    for period_no in range(1, count + 1):
        end = current + timedelta(minutes=duration)
        result.append((current.strftime("%H:%M"), end.strftime("%H:%M")))
        current = end
        if config.get("break_after_period") and period_no == int(config["break_after_period"]):
            current += timedelta(minutes=int(config.get("break_duration_min") or 0))
        if config.get("lunch_after_period") and period_no == int(config["lunch_after_period"]):
            current += timedelta(minutes=int(config.get("lunch_duration_min") or 0))
    return result


def list_subjects(conn, active_only: bool = False) -> list[dict]:
    where = " WHERE is_active=1" if active_only else ""
    return _rows(conn.execute(f"SELECT * FROM tt_subjects{where} ORDER BY name"))


def get_subject(conn, subject_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM tt_subjects WHERE id=?", (subject_id,)))


def save_subject(conn, values: dict) -> int:
    subject_id = values.get("id")
    payload = (_require_text(values.get("name"), "Subject name"), _require_text(values.get("code"), "Subject code").upper(), int(bool(values.get("is_lab"))), int(values.get("is_active", 1)))
    if subject_id:
        old = get_subject(conn, int(subject_id))
        conn.execute("UPDATE tt_subjects SET name=?,code=?,is_lab=?,is_active=? WHERE id=?", (*payload, int(subject_id)))
        _audit(conn, "TIMETABLE_SUBJECT_UPDATE", "tt_subjects", subject_id, old, dict(values))
        return int(subject_id)
    cursor = conn.execute("INSERT INTO tt_subjects(name,code,is_lab,is_active,created_at) VALUES(?,?,?,?,?)", (*payload, now_str()))
    _audit(conn, "TIMETABLE_SUBJECT_CREATE", "tt_subjects", cursor.lastrowid, None, dict(values))
    return int(cursor.lastrowid)


def delete_subject(conn, subject_id: int) -> None:
    old = get_subject(conn, subject_id)
    conn.execute("DELETE FROM tt_subjects WHERE id=?", (subject_id,))
    _audit(conn, "TIMETABLE_SUBJECT_DELETE", "tt_subjects", subject_id, old, None)


def list_teachers(conn, active_only: bool = False) -> list[dict]:
    where = " WHERE is_active=1" if active_only else ""
    return _rows(conn.execute(f"SELECT * FROM tt_teachers{where} ORDER BY name"))


def get_teacher(conn, teacher_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM tt_teachers WHERE id=?", (teacher_id,)))


def save_teacher(conn, values: dict) -> int:
    teacher_id = values.get("id")
    payload = (_require_text(values.get("name"), "Teacher name"), str(values.get("phone") or "").strip(), int(values.get("max_periods_day") or 6), int(values.get("is_active", 1)))
    if payload[2] <= 0:
        raise ValueError("Maximum periods per day must be positive.")
    if teacher_id:
        old = get_teacher(conn, int(teacher_id))
        conn.execute("UPDATE tt_teachers SET name=?,phone=?,max_periods_day=?,is_active=? WHERE id=?", (*payload, int(teacher_id)))
        _audit(conn, "TIMETABLE_TEACHER_UPDATE", "tt_teachers", teacher_id, old, dict(values))
        return int(teacher_id)
    cursor = conn.execute("INSERT INTO tt_teachers(name,phone,max_periods_day,is_active,created_at) VALUES(?,?,?,?,?)", (*payload, now_str()))
    _audit(conn, "TIMETABLE_TEACHER_CREATE", "tt_teachers", cursor.lastrowid, None, dict(values))
    return int(cursor.lastrowid)


def delete_teacher(conn, teacher_id: int) -> None:
    old = get_teacher(conn, teacher_id)
    conn.execute("DELETE FROM tt_teachers WHERE id=?", (teacher_id,))
    _audit(conn, "TIMETABLE_TEACHER_DELETE", "tt_teachers", teacher_id, old, None)


def list_teacher_availability(conn, teacher_id: int) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM tt_teacher_availability WHERE teacher_id=? ORDER BY CASE day WHEN 'MON' THEN 1 WHEN 'TUE' THEN 2 WHEN 'WED' THEN 3 WHEN 'THU' THEN 4 WHEN 'FRI' THEN 5 ELSE 6 END", (teacher_id,)))


def save_teacher_availability(conn, teacher_id: int, rows: Iterable[dict]) -> None:
    old = list_teacher_availability(conn, teacher_id)
    conn.execute("DELETE FROM tt_teacher_availability WHERE teacher_id=?", (teacher_id,))
    saved = []
    for row in rows:
        day = str(row["day"]).upper()
        arrives, departs = str(row["arrives"]), str(row["departs"])
        if day not in DAYS or _time(arrives) >= _time(departs):
            raise ValueError(f"Invalid availability window for {day}.")
        conn.execute("INSERT INTO tt_teacher_availability(teacher_id,day,arrives,departs) VALUES(?,?,?,?)", (teacher_id, day, arrives, departs))
        saved.append({"day": day, "arrives": arrives, "departs": departs})
    _audit(conn, "TIMETABLE_AVAILABILITY_SAVE", "tt_teacher_availability", teacher_id, old, saved)


def list_teacher_constraints(conn, teacher_id: int) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM tt_teacher_constraints WHERE teacher_id=? ORDER BY day,period_no", (teacher_id,)))


def save_teacher_constraints(conn, teacher_id: int, rows: Iterable[dict]) -> None:
    old = list_teacher_constraints(conn, teacher_id)
    conn.execute("DELETE FROM tt_teacher_constraints WHERE teacher_id=?", (teacher_id,))
    saved = []
    for row in rows:
        ctype = str(row.get("ctype") or "").upper()
        if not ctype:
            continue
        day, period_no = str(row["day"]).upper(), int(row["period_no"])
        if day not in DAYS or ctype not in CONSTRAINT_TYPES:
            raise ValueError("Invalid teacher constraint.")
        conn.execute("INSERT INTO tt_teacher_constraints(teacher_id,day,period_no,ctype) VALUES(?,?,?,?)", (teacher_id, day, period_no, ctype))
        saved.append({"day": day, "period_no": period_no, "ctype": ctype})
    _audit(conn, "TIMETABLE_CONSTRAINTS_SAVE", "tt_teacher_constraints", teacher_id, old, saved)


def list_assignments(conn) -> list[dict]:
    return _rows(conn.execute("""SELECT a.*,t.name teacher_name,s.name subject_name,s.code subject_code
        FROM tt_assignments a JOIN tt_teachers t ON t.id=a.teacher_id JOIN tt_subjects s ON s.id=a.subject_id
        ORDER BY a.class_name,s.name,t.name"""))


def save_assignment(conn, teacher_id: int, subject_id: int, class_name: str) -> None:
    values = (int(teacher_id), int(subject_id), _require_text(class_name, "Class"))
    conn.execute("INSERT OR IGNORE INTO tt_assignments(teacher_id,subject_id,class_name) VALUES(?,?,?)", values)
    _audit(conn, "TIMETABLE_ASSIGNMENT_SAVE", "tt_assignments", ":".join(map(str, values)), None, {"teacher_id": values[0], "subject_id": values[1], "class_name": values[2]})


def delete_assignment(conn, teacher_id: int, subject_id: int, class_name: str) -> None:
    old = {"teacher_id": teacher_id, "subject_id": subject_id, "class_name": class_name}
    conn.execute("DELETE FROM tt_assignments WHERE teacher_id=? AND subject_id=? AND class_name=?", (teacher_id, subject_id, class_name))
    _audit(conn, "TIMETABLE_ASSIGNMENT_DELETE", "tt_assignments", f"{teacher_id}:{subject_id}:{class_name}", old, None)


def list_requirements(conn) -> list[dict]:
    return _rows(conn.execute("""SELECT r.*,s.name subject_name,s.code subject_code FROM tt_subject_requirements r
        JOIN tt_subjects s ON s.id=r.subject_id ORDER BY r.class_name,s.name"""))


def get_requirement(conn, subject_id: int, class_name: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM tt_subject_requirements WHERE subject_id=? AND class_name=?", (subject_id, class_name)))


def save_requirement(conn, subject_id: int, class_name: str, periods_per_week: int, double_period_allowed: bool = False) -> None:
    old = get_requirement(conn, subject_id, class_name)
    payload = {"subject_id": int(subject_id), "class_name": _require_text(class_name, "Class"), "periods_per_week": int(periods_per_week), "double_period_allowed": int(bool(double_period_allowed))}
    if payload["periods_per_week"] < 0:
        raise ValueError("Periods per week cannot be negative.")
    conn.execute("""INSERT INTO tt_subject_requirements(subject_id,class_name,periods_per_week,double_period_allowed)
        VALUES(?,?,?,?) ON CONFLICT(subject_id,class_name) DO UPDATE SET periods_per_week=excluded.periods_per_week,double_period_allowed=excluded.double_period_allowed""", tuple(payload.values()))
    _audit(conn, "TIMETABLE_REQUIREMENT_SAVE", "tt_subject_requirements", f"{subject_id}:{class_name}", old, payload)


def delete_requirement(conn, subject_id: int, class_name: str) -> None:
    old = get_requirement(conn, subject_id, class_name)
    conn.execute("DELETE FROM tt_subject_requirements WHERE subject_id=? AND class_name=?", (subject_id, class_name))
    _audit(conn, "TIMETABLE_REQUIREMENT_DELETE", "tt_subject_requirements", f"{subject_id}:{class_name}", old, None)


def teacher_available_periods(conn, teacher_id: int, config: dict) -> dict[str, list[int]]:
    """Map availability windows to periods wholly contained by each window."""
    days = [day for day in str(config["working_days"]).split(",") if day]
    all_periods = list(range(1, int(config["periods_per_day"]) + 1))
    rows = list_teacher_availability(conn, teacher_id)
    if not rows:
        return {day: list(all_periods) for day in days}
    windows = {row["day"]: row for row in rows}
    times = period_times(config)
    result = {}
    for day in days:
        window = windows.get(day)
        if window is None:
            result[day] = []
            continue
        arrives, departs = _time(window["arrives"]), _time(window["departs"])
        result[day] = [index for index, (start, end) in enumerate(times, 1) if _time(start) >= arrives and _time(end) <= departs]
    return result


def list_versions(conn) -> list[dict]:
    return _rows(conn.execute("""SELECT v.*,u.username generated_by_name FROM tt_versions v
        LEFT JOIN users u ON u.id=v.generated_by ORDER BY v.id DESC"""))


def get_version(conn, version_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM tt_versions WHERE id=?", (version_id,)))


def create_version(conn, label: str, academic_year: str, generated_by: int | None = None) -> int:
    cursor = conn.execute("INSERT INTO tt_versions(label,academic_year,generated_at,generated_by,is_published) VALUES(?,?,?,?,0)", (_require_text(label, "Version label"), _require_text(academic_year, "Academic year"), now_str(), generated_by or _user_id()))
    _audit(conn, "TIMETABLE_VERSION_CREATE", "tt_versions", cursor.lastrowid, None, {"label": label, "academic_year": academic_year})
    return int(cursor.lastrowid)


def save_timetable_slots(conn, version_id: int, slots: Iterable[dict]) -> None:
    conn.execute("DELETE FROM tt_timetable WHERE version_id=?", (version_id,))
    count = 0
    for slot in slots:
        conn.execute("""INSERT INTO tt_timetable(version_id,class_name,day,period_no,subject_id,teacher_id,is_free,is_locked)
            VALUES(?,?,?,?,?,?,?,?)""", (version_id, slot["class_name"], slot["day"], int(slot["period_no"]), slot.get("subject_id"), slot.get("teacher_id"), int(bool(slot.get("is_free"))), int(bool(slot.get("is_locked")))))
        count += 1
    _audit(conn, "TIMETABLE_SLOTS_SAVE", "tt_timetable", version_id, None, {"slot_count": count})


def list_timetable(conn, version_id: int, class_name: str | None = None, teacher_id: int | None = None) -> list[dict]:
    clauses, params = ["x.version_id=?"], [version_id]
    if class_name is not None:
        clauses.append("x.class_name=?"); params.append(class_name)
    if teacher_id is not None:
        clauses.append("x.teacher_id=?"); params.append(teacher_id)
    return _rows(conn.execute(f"""SELECT x.*,s.name subject_name,s.code subject_code,t.name teacher_name
        FROM tt_timetable x LEFT JOIN tt_subjects s ON s.id=x.subject_id LEFT JOIN tt_teachers t ON t.id=x.teacher_id
        WHERE {' AND '.join(clauses)} ORDER BY x.class_name,CASE x.day WHEN 'MON' THEN 1 WHEN 'TUE' THEN 2 WHEN 'WED' THEN 3 WHEN 'THU' THEN 4 WHEN 'FRI' THEN 5 ELSE 6 END,x.period_no""", params))


def validate_slot(conn, version_id: int, class_name: str, day: str, period_no: int, subject_id: int | None, teacher_id: int | None, exclude_current: bool = True) -> None:
    if subject_id is None or teacher_id is None:
        return
    assignment = conn.execute("SELECT 1 FROM tt_assignments WHERE teacher_id=? AND subject_id=? AND class_name=?", (teacher_id, subject_id, class_name)).fetchone()
    if assignment is None:
        raise ValueError("The selected teacher is not assigned to this subject and class.")
    config = get_schedule_config(conn)
    if period_no not in teacher_available_periods(conn, teacher_id, config).get(day, []):
        raise ValueError("The teacher is unavailable during this period.")
    if conn.execute("SELECT 1 FROM tt_teacher_constraints WHERE teacher_id=? AND day=? AND period_no=? AND ctype='UNAVAILABLE'", (teacher_id, day, period_no)).fetchone():
        raise ValueError("The teacher marked this period unavailable.")
    sql = "SELECT class_name FROM tt_timetable WHERE version_id=? AND teacher_id=? AND day=? AND period_no=?"
    params: list = [version_id, teacher_id, day, period_no]
    if exclude_current:
        sql += " AND class_name<>?"; params.append(class_name)
    if conn.execute(sql, params).fetchone():
        raise ValueError("The teacher is already assigned to another class in this period.")
    teacher = get_teacher(conn, teacher_id)
    assigned = conn.execute("SELECT COUNT(*) FROM tt_timetable WHERE version_id=? AND teacher_id=? AND day=? AND class_name<>?", (version_id, teacher_id, day, class_name)).fetchone()[0]
    if teacher and assigned >= int(teacher["max_periods_day"]):
        raise ValueError("The teacher has reached the daily period limit.")


def save_timetable_cell(conn, version_id: int, class_name: str, day: str, period_no: int, subject_id: int | None, teacher_id: int | None, is_locked: bool = True) -> None:
    old = _row(conn.execute("SELECT * FROM tt_timetable WHERE version_id=? AND class_name=? AND day=? AND period_no=?", (version_id, class_name, day, period_no)))
    validate_slot(conn, version_id, class_name, day, period_no, subject_id, teacher_id)
    payload = {"subject_id": subject_id, "teacher_id": teacher_id, "is_free": int(subject_id is None), "is_locked": int(bool(is_locked))}
    conn.execute("""INSERT INTO tt_timetable(version_id,class_name,day,period_no,subject_id,teacher_id,is_free,is_locked)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(version_id,class_name,day,period_no) DO UPDATE SET
        subject_id=excluded.subject_id,teacher_id=excluded.teacher_id,is_free=excluded.is_free,is_locked=excluded.is_locked""", (version_id, class_name, day, period_no, subject_id, teacher_id, payload["is_free"], payload["is_locked"]))
    _audit(conn, "TIMETABLE_CELL_SAVE", "tt_timetable", f"{version_id}:{class_name}:{day}:{period_no}", old, payload)


def set_cell_lock(conn, version_id: int, class_name: str, day: str, period_no: int, locked: bool) -> None:
    conn.execute("UPDATE tt_timetable SET is_locked=? WHERE version_id=? AND class_name=? AND day=? AND period_no=?", (int(bool(locked)), version_id, class_name, day, period_no))
    _audit(conn, "TIMETABLE_CELL_LOCK", "tt_timetable", f"{version_id}:{class_name}:{day}:{period_no}", None, {"is_locked": int(bool(locked))})


def publish_version(conn, version_id: int) -> None:
    old = get_version(conn, version_id)
    if old is None:
        raise ValueError("Timetable version was not found.")
    conn.execute("UPDATE tt_versions SET is_published=0 WHERE academic_year=?", (old["academic_year"],))
    conn.execute("UPDATE tt_versions SET is_published=1 WHERE id=?", (version_id,))
    _audit(conn, "TIMETABLE_VERSION_PUBLISH", "tt_versions", version_id, old, {**old, "is_published": 1})


def delete_version(conn, version_id: int) -> None:
    old = get_version(conn, version_id)
    conn.execute("DELETE FROM tt_versions WHERE id=?", (version_id,))
    _audit(conn, "TIMETABLE_VERSION_DELETE", "tt_versions", version_id, old, None)


def build_problem(conn) -> dict:
    """Return a self-contained solver problem with no future DB dependency."""
    config = get_schedule_config(conn)
    days = [day for day in str(config["working_days"]).split(",") if day]
    periods = list(range(1, int(config["periods_per_day"]) + 1))
    classes = [row[0] for row in conn.execute("SELECT name FROM classes WHERE is_active=1 ORDER BY name")]
    subjects = {row["id"]: row for row in list_subjects(conn, True)}
    teachers = {row["id"]: row for row in list_teachers(conn, True)}
    requirements = [row for row in list_requirements(conn) if row["class_name"] in classes and row["subject_id"] in subjects]
    assignments: dict[tuple[str, int], list[int]] = {}
    for row in list_assignments(conn):
        if row["teacher_id"] in teachers and row["subject_id"] in subjects:
            assignments.setdefault((row["class_name"], row["subject_id"]), []).append(row["teacher_id"])
    availability = {teacher_id: teacher_available_periods(conn, teacher_id, config) for teacher_id in teachers}
    constraints: dict[int, dict[str, dict[int, str]]] = {}
    for teacher_id in teachers:
        for row in list_teacher_constraints(conn, teacher_id):
            constraints.setdefault(teacher_id, {}).setdefault(row["day"], {})[int(row["period_no"])] = row["ctype"]
    return {
        "classes": classes, "days": days, "periods": periods,
        "period_times": period_times(config), "subjects": subjects, "teachers": teachers,
        "requirements": requirements, "assignments": assignments,
        "availability": availability, "constraints": constraints,
    }
