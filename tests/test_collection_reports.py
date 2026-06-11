"""Clean collection report filtering and PDF generation coverage."""

from pathlib import Path
import sqlite3

import pytest

import report_generator
from report_generator import collection_report, collection_report_rows


def report_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE payments(
            id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,amount_paid REAL,
            payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,
            cheque_status TEXT
        );
        INSERT INTO settings VALUES('school_name','Test School');
        INSERT INTO users VALUES(1,'Sonali');
        INSERT INTO students VALUES(1,'Asha');
        INSERT INTO students VALUES(2,'Ravi');
        INSERT INTO payments VALUES(1,1,'R1',100,'01-06-2026',1,'CASH','',NULL);
        INSERT INTO payments VALUES(2,1,'R1',50,'01-06-2026',1,'CASH','',NULL);
        INSERT INTO payments VALUES(3,2,'R2',200,'02-06-2026',1,'UPI','',NULL);
        INSERT INTO payments VALUES(4,2,'R3',300,'03-06-2026',1,'CHEQUE','', 'PENDING');
        INSERT INTO payments VALUES(5,1,'VOID',-100,'02-06-2026',1,'CASH','VOID of R1',NULL);
        """
    )
    return conn


def test_collection_rows_group_by_student_receipt_without_fee_heads():
    rows = collection_report_rows(
        report_db(), "01-06-2026", "02-06-2026", ("CASH", "UPI")
    )

    assert rows == [
        {"receipt_no": "R1", "student": "Asha", "date": "01-06-2026", "amount": 150.0, "modes": ["CASH"], "collectors": ["Sonali"]},
        {"receipt_no": "R2", "student": "Ravi", "date": "02-06-2026", "amount": 200.0, "modes": ["UPI"], "collectors": ["Sonali"]},
    ]
    assert all("fee_head" not in row for row in rows)


def test_collection_rows_support_single_or_multiple_payment_modes():
    conn = report_db()
    cash_only = collection_report_rows(conn, "01-06-2026", "03-06-2026", ("CASH",))
    all_modes = collection_report_rows(conn, "01-06-2026", "03-06-2026", ("CASH", "UPI", "CHEQUE"))

    assert [row["receipt_no"] for row in cash_only] == ["R1"]
    assert [row["receipt_no"] for row in all_modes] == ["R1", "R2", "R3"]


def test_collection_report_validates_dates_and_mode_selection():
    conn = report_db()
    with pytest.raises(ValueError, match="Select at least one"):
        collection_report_rows(conn, "01-06-2026", "02-06-2026", ())
    with pytest.raises(ValueError, match="cannot be after"):
        collection_report_rows(conn, "03-06-2026", "02-06-2026", ("CASH",))


def test_collection_report_generates_pdf_with_optional_columns_and_signatory(tmp_path, monkeypatch):
    monkeypatch.setattr(report_generator, "REPORTS_DIR", str(tmp_path))
    path = Path(collection_report(
        report_db(), "01-06-2026", "03-06-2026", ("CASH", "UPI", "CHEQUE"),
        True, True, True, True, "Sonali Sahu",
    ))

    assert path.exists()
    assert path.stat().st_size > 0


def test_since_last_report_uses_and_updates_per_mode_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(report_generator, "REPORTS_DIR", str(tmp_path))
    conn = report_db()

    first = Path(collection_report(
        conn, "", "", ("CASH", "UPI", "CHEQUE"),
        True, False, False, False,
        "Mr. L.P. Sahu, Sanskriti Vidhya Mandir High School, Bareli",
        "SINCE_LAST",
    ))

    assert first.exists()
    checkpoints = dict(conn.execute(
        "SELECT key,value FROM settings WHERE key LIKE 'collection_report_last_payment_id_%'"
    ).fetchall())
    assert checkpoints == {
        "collection_report_last_payment_id_cash": "2",
        "collection_report_last_payment_id_cheque": "4",
        "collection_report_last_payment_id_upi": "3",
    }

    conn.execute("INSERT INTO payments VALUES(6,1,'R4',75,'04-06-2026',1,'CASH','',NULL)")
    rows, previous, latest, previous_generated_at = report_generator.collection_report_rows_since_last(
        conn, ("CASH", "UPI", "CHEQUE")
    )

    assert [row["receipt_no"] for row in rows] == ["R4"]
    assert previous == {"CASH": 2, "CHEQUE": 4, "UPI": 3}
    assert latest == {"CASH": 6, "CHEQUE": 4, "UPI": 3}
    assert previous_generated_at


def test_daily_report_establishes_since_last_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(report_generator, "REPORTS_DIR", str(tmp_path))
    conn = report_db()

    collection_report(
        conn, "01-06-2026", "01-06-2026", ("CASH",),
        report_collected_by="Varun Sahu, Sanskriti Vidhya Mandir, Bareli",
        report_type="DAILY",
    )

    assert conn.execute(
        "SELECT value FROM settings WHERE key='collection_report_last_payment_id_cash'"
    ).fetchone()[0] == "2"


def test_custom_historical_report_does_not_move_since_last_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(report_generator, "REPORTS_DIR", str(tmp_path))
    conn = report_db()

    collection_report(
        conn, "01-06-2026", "03-06-2026", ("CASH",),
        report_collected_by="Varun Sahu",
        report_type="CUSTOM",
    )

    assert conn.execute(
        "SELECT value FROM settings WHERE key='collection_report_last_payment_id_cash'"
    ).fetchone() is None
