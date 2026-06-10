"""Class/section and complete student-profile migration coverage."""

import sqlite3

from migrations import migration_v009_class_section_and_student_details


def legacy_student_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE students(
            id INTEGER PRIMARY KEY,name TEXT NOT NULL,class TEXT,section TEXT,
            aadhaar TEXT UNIQUE,phone TEXT,guardian_name TEXT,is_active INTEGER,
            status TEXT,created_at TEXT
        );
        INSERT INTO students(name,class,section,guardian_name,is_active,status)
        VALUES('Student One','Class 1','A','Parent One',1,'ACTIVE');
        """
    )
    return conn


def test_student_profile_migration_backfills_master_data_and_father_name():
    conn = legacy_student_db()
    migration_v009_class_section_and_student_details(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
    assert {
        "scholar_no", "ekyc_status", "serial_no", "father_name", "mother_name",
        "address", "dob", "admission_date", "mobile2", "sssm_id", "gender", "category",
    } <= columns
    assert conn.execute("SELECT name FROM classes").fetchone()[0] == "Class 1"
    assert conn.execute(
        "SELECT s.name FROM sections s JOIN classes c ON c.id=s.class_id WHERE c.name='Class 1'"
    ).fetchone()[0] == "A"
    assert conn.execute("SELECT father_name FROM students").fetchone()[0] == "Parent One"


def test_scholar_number_is_unique_when_present():
    conn = legacy_student_db()
    migration_v009_class_section_and_student_details(conn)
    conn.execute("UPDATE students SET scholar_no='SCH-1' WHERE id=1")
    conn.execute("INSERT INTO students(name,scholar_no) VALUES('Student Two',NULL)")
    conn.execute("INSERT INTO students(name,scholar_no) VALUES('Student Three',NULL)")

    try:
        conn.execute("INSERT INTO students(name,scholar_no) VALUES('Duplicate','SCH-1')")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate scholar number was accepted")
