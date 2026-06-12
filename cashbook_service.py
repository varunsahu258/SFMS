"""Cashbook persistence, collection import, bank-statement analysis, and reports."""

from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from config import REPORTS_DIR
from utils import now_str

PAYMENT_METHODS = ("CASH", "CHEQUE", "UPI", "BANK TRANSFER")
ACCOUNT_CASH = "CASH"
DEFAULT_ACCOUNTS = (ACCOUNT_CASH, "CBI", "Gramin Bank", "FDR")
DEFAULT_HEADS = (
    ("Fee Collection", "INCOME"),
    ("Donation", "INCOME"),
    ("Interest", "INCOME"),
    ("Salary", "EXPENSE"),
    ("Office Expense", "EXPENSE"),
    ("Maintenance", "EXPENSE"),
    ("Vehicle Fuel", "VEHICLE"),
    ("Vehicle Repair", "VEHICLE"),
    ("Vehicle Insurance", "VEHICLE"),
)
DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d/%b/%Y")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def parse_date(value: object) -> str:
    """Normalize supported date values to DD-MM-YYYY."""
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%d-%m-%Y")
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text.split()[0], date_format).strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise ValueError("Date must be DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YYYY.")


def parse_amount(value: object) -> Decimal:
    """Parse a positive currency amount."""
    text = re.sub(r"[^0-9.\-]", "", str(value or "").strip())
    try:
        amount = Decimal(text or "0")
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Amount must be a valid number.") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount cannot have more than two decimal places.")
    return amount


def install_cashbook_schema(conn: sqlite3.Connection) -> None:
    """Create cashbook tables and seed default accounts/heads."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cashbook_heads(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL CHECK(category IN ('INCOME','EXPENSE','VEHICLE')),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS cashbook_accounts(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            opening_balance REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS cashbook_transactions(
            id INTEGER PRIMARY KEY,
            txn_date TEXT NOT NULL,
            txn_type TEXT NOT NULL CHECK(txn_type IN ('INCOME','EXPENSE')),
            head_id INTEGER NOT NULL,
            description TEXT,
            amount REAL NOT NULL CHECK(amount > 0),
            payment_method TEXT NOT NULL CHECK(payment_method IN ('CASH','CHEQUE','UPI','BANK TRANSFER')),
            account_name TEXT NOT NULL DEFAULT 'CASH',
            reference TEXT,
            counterparty TEXT,
            source_type TEXT NOT NULL DEFAULT 'MANUAL',
            source_id INTEGER,
            receipt_no TEXT,
            student_id INTEGER,
            voucher_no TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY(head_id) REFERENCES cashbook_heads(id),
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(created_by) REFERENCES users(id),
            UNIQUE(source_type, source_id)
        );
        CREATE TABLE IF NOT EXISTS bank_statement_imports(
            id INTEGER PRIMARY KEY,
            bank_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            imported_by INTEGER,
            notes TEXT,
            FOREIGN KEY(imported_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS bank_statement_rows(
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            txn_date TEXT,
            description TEXT,
            debit REAL NOT NULL DEFAULT 0,
            credit REAL NOT NULL DEFAULT 0,
            balance REAL,
            reference TEXT,
            matched_transaction_id INTEGER,
            analysis_note TEXT,
            FOREIGN KEY(import_id) REFERENCES bank_statement_imports(id),
            FOREIGN KEY(matched_transaction_id) REFERENCES cashbook_transactions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cashbook_txn_date ON cashbook_transactions(txn_date, txn_type);
        CREATE INDEX IF NOT EXISTS idx_cashbook_head ON cashbook_transactions(head_id);
        CREATE INDEX IF NOT EXISTS idx_cashbook_account ON cashbook_transactions(account_name);
        CREATE INDEX IF NOT EXISTS idx_bank_statement_import ON bank_statement_rows(import_id);
        """
    )
    now = now_str()
    for account in DEFAULT_ACCOUNTS:
        conn.execute(
            "INSERT OR IGNORE INTO cashbook_accounts(name,opening_balance,is_active,created_at) VALUES(?,0,1,?)",
            (account, now),
        )
    for name, category in DEFAULT_HEADS:
        conn.execute(
            "INSERT OR IGNORE INTO cashbook_heads(name,category,is_active,created_at) VALUES(?,?,1,?)",
            (name, category, now),
        )


def ensure_student_extra_columns(conn: sqlite3.Connection) -> None:
    """Add additional student information columns requested for detail screens."""
    existing = _columns(conn, "students")
    additions = {
        "is_rte": "INTEGER NOT NULL DEFAULT 0",
        "father_education": "TEXT",
        "father_occupation": "TEXT",
        "family_annual_income": "REAL",
        "mother_education": "TEXT",
        "mother_occupation": "TEXT",
        "conveyance_details": "TEXT",
        "bank_account_number": "TEXT",
        "ifsc_code": "TEXT",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE students ADD COLUMN {column} {ddl}")


def list_heads(conn: sqlite3.Connection, category: str | None = None, active_only: bool = True) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[object] = []
    if category:
        if category == "EXPENSE":
            where.append("category IN ('EXPENSE','VEHICLE')")
        else:
            where.append("category=?")
            params.append(category)
    if active_only:
        where.append("is_active=1")
    sql = "SELECT * FROM cashbook_heads"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY category,name"
    return conn.execute(sql, params).fetchall()


def upsert_head(conn: sqlite3.Connection, name: str, category: str, user_id: int | None, head_id: int | None = None) -> int:
    """Create or update an income/expense/vehicle head."""
    name = str(name or "").strip()
    category = str(category or "").strip().upper()
    if not name:
        raise ValueError("Head name is required.")
    if category not in {"INCOME", "EXPENSE", "VEHICLE"}:
        raise ValueError("Head category must be Income, Expense, or Vehicle.")
    if head_id:
        conn.execute("UPDATE cashbook_heads SET name=?,category=? WHERE id=?", (name, category, head_id))
        return int(head_id)
    cursor = conn.execute(
        "INSERT INTO cashbook_heads(name,category,is_active,created_at,created_by) VALUES(?,?,1,?,?)",
        (name, category, now_str(), user_id),
    )
    return int(cursor.lastrowid)


def set_head_active(conn: sqlite3.Connection, head_id: int, active: bool) -> None:
    conn.execute("UPDATE cashbook_heads SET is_active=? WHERE id=?", (1 if active else 0, head_id))


def add_account(conn: sqlite3.Connection, name: str, opening_balance: object = 0, user_id: int | None = None) -> int:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Account name is required.")
    try:
        opening = Decimal(str(opening_balance or "0"))
    except InvalidOperation as exc:
        raise ValueError("Opening balance must be a valid number.") from exc
    cursor = conn.execute(
        "INSERT INTO cashbook_accounts(name,opening_balance,is_active,created_at,created_by) VALUES(?,?,?,?,?)",
        (name, str(opening), 1, now_str(), user_id),
    )
    return int(cursor.lastrowid)


def list_accounts(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM cashbook_accounts WHERE is_active=1 ORDER BY name")]


def add_transaction(
    conn: sqlite3.Connection,
    *,
    txn_date: object,
    txn_type: str,
    head_id: int,
    description: str,
    amount: object,
    payment_method: str,
    account_name: str,
    reference: str = "",
    counterparty: str = "",
    source_type: str = "MANUAL",
    source_id: int | None = None,
    receipt_no: str | None = None,
    student_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Insert one cashbook income/expense transaction."""
    txn_type = str(txn_type or "").strip().upper()
    payment_method = str(payment_method or "").strip().upper()
    if txn_type not in {"INCOME", "EXPENSE"}:
        raise ValueError("Transaction type must be Income or Expense.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Payment method must be Cash, Cheque, UPI, or Bank Transfer.")
    account_name = str(account_name or ACCOUNT_CASH).strip() or ACCOUNT_CASH
    parsed_date = parse_date(txn_date)
    parsed_amount = parse_amount(amount)
    if not conn.execute("SELECT 1 FROM cashbook_accounts WHERE name=?", (account_name,)).fetchone():
        conn.execute(
            "INSERT INTO cashbook_accounts(name,opening_balance,is_active,created_at,created_by) VALUES(?,0,1,?,?)",
            (account_name, now_str(), user_id),
        )
    cursor = conn.execute(
        """
        INSERT INTO cashbook_transactions(
            txn_date,txn_type,head_id,description,amount,payment_method,account_name,
            reference,counterparty,source_type,source_id,receipt_no,student_id,voucher_no,created_at,created_by
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            parsed_date, txn_type, head_id, str(description or "").strip(), str(parsed_amount),
            payment_method, account_name, str(reference or "").strip(), str(counterparty or "").strip(),
            source_type, source_id, receipt_no, student_id, None, now_str(), user_id,
        ),
    )
    voucher = f"PV-{cursor.lastrowid:06d}" if txn_type == "EXPENSE" else f"RV-{cursor.lastrowid:06d}"
    conn.execute("UPDATE cashbook_transactions SET voucher_no=? WHERE id=?", (voucher, cursor.lastrowid))
    return int(cursor.lastrowid)


def _date_sql(column: str) -> str:
    return f"date(substr({column},7,4)||'-'||substr({column},4,2)||'-'||substr({column},1,2))"


def transactions(conn: sqlite3.Connection, start_date: object | None = None, end_date: object | None = None,
                 search: str = "", head_id: int | None = None, account_name: str = "") -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[object] = []
    if start_date:
        where.append(f"{_date_sql('t.txn_date')} >= date(?)")
        params.append(datetime.strptime(parse_date(start_date), "%d-%m-%Y").strftime("%Y-%m-%d"))
    if end_date:
        where.append(f"{_date_sql('t.txn_date')} <= date(?)")
        params.append(datetime.strptime(parse_date(end_date), "%d-%m-%Y").strftime("%Y-%m-%d"))
    if search:
        term = f"%{search.strip()}%"
        where.append("(t.description LIKE ? OR t.reference LIKE ? OR t.receipt_no LIKE ? OR t.counterparty LIKE ? OR h.name LIKE ?)")
        params.extend([term] * 5)
    if head_id:
        where.append("t.head_id=?")
        params.append(head_id)
    if account_name:
        where.append("t.account_name=?")
        params.append(account_name)
    sql = """
        SELECT t.*, h.name AS head_name, h.category AS head_category, s.name AS student_name
        FROM cashbook_transactions t
        JOIN cashbook_heads h ON h.id=t.head_id
        LEFT JOIN students s ON s.id=t.student_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {_date_sql('t.txn_date')} DESC,t.id DESC"
    return conn.execute(sql, params).fetchall()


def summary(conn: sqlite3.Connection, start_date: object, end_date: object) -> dict:
    """Return previous balance, period totals, closing balance, and account balances."""
    start = datetime.strptime(parse_date(start_date), "%d-%m-%Y").strftime("%Y-%m-%d")
    end = datetime.strptime(parse_date(end_date), "%d-%m-%Y").strftime("%Y-%m-%d")
    previous = Decimal("0")
    income = Decimal("0")
    expense = Decimal("0")
    account_balances: dict[str, Decimal] = {}
    for name, opening in conn.execute("SELECT name,opening_balance FROM cashbook_accounts WHERE is_active=1"):
        value = Decimal(str(opening or 0))
        account_balances[str(name)] = value
        previous += value
    for row in conn.execute(f"SELECT txn_type,account_name,amount,{_date_sql('txn_date')} AS d FROM cashbook_transactions"):
        amount = Decimal(str(row[2] or 0))
        if row[3] < start:
            previous += amount if row[0] == "INCOME" else -amount
        if start <= row[3] <= end:
            if row[0] == "INCOME":
                income += amount
            else:
                expense += amount
        account = str(row[1] or ACCOUNT_CASH)
        account_balances.setdefault(account, Decimal("0"))
        account_balances[account] += amount if row[0] == "INCOME" else -amount
    return {
        "previous_balance": previous,
        "income_total": income,
        "expense_total": expense,
        "balance": previous + income - expense,
        "account_balances": account_balances,
    }


def vehicle_expenses_by_head(conn: sqlite3.Connection, start_date: object | None = None, end_date: object | None = None) -> list[sqlite3.Row]:
    where = ["h.category='VEHICLE'", "t.txn_type='EXPENSE'"]
    params: list[object] = []
    if start_date:
        where.append(f"{_date_sql('t.txn_date')} >= date(?)")
        params.append(datetime.strptime(parse_date(start_date), "%d-%m-%Y").strftime("%Y-%m-%d"))
    if end_date:
        where.append(f"{_date_sql('t.txn_date')} <= date(?)")
        params.append(datetime.strptime(parse_date(end_date), "%d-%m-%Y").strftime("%Y-%m-%d"))
    return conn.execute(
        f"""
        SELECT h.name, COUNT(t.id) AS count, COALESCE(SUM(t.amount),0) AS total
        FROM cashbook_transactions t JOIN cashbook_heads h ON h.id=t.head_id
        WHERE {' AND '.join(where)} GROUP BY h.id,h.name ORDER BY h.name
        """,
        params,
    ).fetchall()


def collection_candidates(conn: sqlite3.Connection, include_main=True, include_small=True, include_exemption=True) -> list[sqlite3.Row]:
    """Return receipts not yet imported into the cashbook."""
    clauses = []
    if include_main:
        clauses.append("UPPER(COALESCE(r.receipt_type,'')) LIKE '%MAIN%'")
    if include_small:
        clauses.append("UPPER(COALESCE(r.receipt_type,'')) LIKE '%SMALL%'")
    if include_exemption:
        clauses.append("UPPER(COALESCE(r.receipt_type,'')) LIKE '%EXEMPTION%'")
    if not clauses:
        return []
    return conn.execute(
        f"""
        SELECT r.id AS receipt_id,r.receipt_no,r.total_paid,r.receipt_type,r.printed_at,
               r.student_id,s.name AS student_name,
               COALESCE(MAX(p.payment_mode),'CASH') AS payment_mode,
               COALESCE(MAX(CASE WHEN UPPER(p.payment_mode)='CHEQUE' THEN p.cheque_number WHEN UPPER(p.payment_mode)='UPI' THEN p.upi_reference ELSE '' END),'') AS reference
        FROM receipts r
        JOIN payments p ON p.receipt_no=r.receipt_no
        LEFT JOIN students s ON s.id=r.student_id
        WHERE ({' OR '.join(clauses)})
          AND NOT EXISTS (SELECT 1 FROM cashbook_transactions t WHERE t.source_type='COLLECTION_RECEIPT' AND t.source_id=r.id)
          AND COALESCE(r.total_paid,0) > 0
        GROUP BY r.id
        ORDER BY r.id DESC
        """
    ).fetchall()


def import_collection_receipts(conn: sqlite3.Connection, receipt_ids: list[int], account_name: str, user_id: int | None) -> int:
    if not receipt_ids:
        return 0
    row = conn.execute("SELECT id FROM cashbook_heads WHERE name='Fee Collection' LIMIT 1").fetchone()
    if row is None:
        head_id = upsert_head(conn, "Fee Collection", "INCOME", user_id)
    else:
        head_id = int(row[0])
    imported = 0
    placeholders = ",".join("?" for _ in receipt_ids)
    rows = conn.execute(
        f"""
        SELECT r.id AS receipt_id,r.receipt_no,r.total_paid,r.receipt_type,r.printed_at,
               r.student_id,s.name AS student_name,
               COALESCE(MAX(p.payment_mode),'CASH') AS payment_mode,
               COALESCE(MAX(CASE WHEN UPPER(p.payment_mode)='CHEQUE' THEN p.cheque_number WHEN UPPER(p.payment_mode)='UPI' THEN p.upi_reference ELSE '' END),'') AS reference
        FROM receipts r JOIN payments p ON p.receipt_no=r.receipt_no
        LEFT JOIN students s ON s.id=r.student_id
        WHERE r.id IN ({placeholders})
        GROUP BY r.id
        """,
        receipt_ids,
    ).fetchall()
    for row in rows:
        if conn.execute("SELECT 1 FROM cashbook_transactions WHERE source_type='COLLECTION_RECEIPT' AND source_id=?", (row["receipt_id"],)).fetchone():
            continue
        method = str(row["payment_mode"] or "CASH").upper()
        if method not in PAYMENT_METHODS:
            method = "CASH"
        add_transaction(
            conn,
            txn_date=str(row["printed_at"] or "")[:10] or datetime.now().strftime("%d-%m-%Y"),
            txn_type="INCOME",
            head_id=head_id,
            description=f"Imported collection receipt {row['receipt_no']} ({row['receipt_type'] or 'Collection'})",
            amount=row["total_paid"],
            payment_method=method,
            account_name=account_name,
            reference=row["reference"] or row["receipt_no"],
            counterparty=row["student_name"] or "",
            source_type="COLLECTION_RECEIPT",
            source_id=int(row["receipt_id"]),
            receipt_no=row["receipt_no"],
            student_id=row["student_id"],
            user_id=user_id,
        )
        imported += 1
    return imported


def import_bank_statement_csv(conn: sqlite3.Connection, path: str, bank_name: str, user_id: int | None) -> tuple[int, int]:
    """Import a CBI/CSV-style bank statement and attempt simple reference matching."""
    bank_name = str(bank_name or "Central Bank of India").strip() or "Central Bank of India"
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    import_id = conn.execute(
        "INSERT INTO bank_statement_imports(bank_name,filename,imported_at,imported_by) VALUES(?,?,?,?)",
        (bank_name, str(source), now_str(), user_id),
    ).lastrowid
    matched = 0
    count = 0
    with source.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        for raw in reader:
            normalized = {re.sub(r"[^a-z0-9]", "", str(k or "").lower()): v for k, v in raw.items()}
            description = str(normalized.get("description") or normalized.get("narration") or normalized.get("particulars") or normalized.get("remarks") or "").strip()
            date_value = normalized.get("date") or normalized.get("valuedate") or normalized.get("transactiondate") or ""
            try:
                txn_date = parse_date(date_value)
            except ValueError:
                txn_date = str(date_value or "").strip()
            debit = _statement_amount(normalized, ("debit", "withdrawal", "dr"))
            credit = _statement_amount(normalized, ("credit", "deposit", "cr"))
            balance = _statement_optional_amount(normalized, ("balance", "closingbalance"))
            reference = _extract_reference(description) or str(normalized.get("referenceno") or normalized.get("utr") or "").strip()
            match_id = _match_bank_row(conn, description, reference, credit, debit)
            if match_id:
                matched += 1
            note = "Matched" if match_id else "Unmatched"
            conn.execute(
                """
                INSERT INTO bank_statement_rows(import_id,txn_date,description,debit,credit,balance,reference,matched_transaction_id,analysis_note)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (import_id, txn_date, description, str(debit), str(credit), str(balance) if balance is not None else None, reference, match_id, note),
            )
            count += 1
    return count, matched


def _statement_amount(row: dict, keys: tuple[str, ...]) -> Decimal:
    for key in keys:
        if row.get(key) not in (None, ""):
            try:
                return abs(Decimal(re.sub(r"[^0-9.\-]", "", str(row[key])) or "0"))
            except InvalidOperation:
                return Decimal("0")
    return Decimal("0")


def _statement_optional_amount(row: dict, keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        if row.get(key) not in (None, ""):
            try:
                return Decimal(re.sub(r"[^0-9.\-]", "", str(row[key])) or "0")
            except InvalidOperation:
                return None
    return None


def _extract_reference(text: str) -> str:
    match = re.search(r"(?:UPI|UTR|REF|NEFT|IMPS)[\s:/-]*([A-Z0-9]{6,})", text.upper())
    return match.group(1) if match else ""


def _match_bank_row(conn: sqlite3.Connection, description: str, reference: str, credit: Decimal, debit: Decimal) -> int | None:
    amount = credit if credit > 0 else debit
    if amount <= 0:
        return None
    params: list[object] = [str(amount)]
    where = ["ABS(t.amount - ?) < 0.005"]
    if reference:
        where.append("(t.reference LIKE ? OR t.receipt_no LIKE ?)")
        params.extend([f"%{reference}%", f"%{reference}%"])
    elif description:
        where.append("t.description LIKE ?")
        params.append(f"%{description[:30]}%")
    else:
        return None
    row = conn.execute(f"SELECT t.id FROM cashbook_transactions t WHERE {' AND '.join(where)} ORDER BY t.id DESC LIMIT 1", params).fetchone()
    return int(row[0]) if row else None


def latest_bank_rows(conn: sqlite3.Connection, limit: int = 300) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, i.bank_name, i.filename FROM bank_statement_rows r
        JOIN bank_statement_imports i ON i.id=r.import_id
        ORDER BY r.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def print_cashbook_report(conn: sqlite3.Connection, start_date: object, end_date: object, path: str | None = None) -> str:
    """Generate a PDF cashbook report for a custom period."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    start = parse_date(start_date)
    end = parse_date(end_date)
    output = Path(path) if path else Path(REPORTS_DIR) / f"cashbook_{start.replace('-', '')}_{end.replace('-', '')}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = [Paragraph("Cashbook", styles["Title"]), Paragraph(f"Period: {start} to {end}", styles["Normal"]), Spacer(1, 6 * mm)]
    state = summary(conn, start, end)
    story.append(Table([
        ["Previous Balance", f"{state['previous_balance']:.2f}"],
        ["Income Total", f"{state['income_total']:.2f}"],
        ["Expense Total", f"{state['expense_total']:.2f}"],
        ["Balance", f"{state['balance']:.2f}"],
    ], colWidths=[70 * mm, 40 * mm]))
    story.append(Spacer(1, 6 * mm))
    rows = [["Date", "Type", "Head", "Description", "Method", "Account", "Amount"]]
    for row in transactions(conn, start, end):
        rows.append([row["txn_date"], row["txn_type"], row["head_name"], row["description"] or "", row["payment_method"], row["account_name"], f"{float(row['amount'] or 0):.2f}"])
    table = Table(rows, repeatRows=1, colWidths=[22 * mm, 18 * mm, 28 * mm, 54 * mm, 25 * mm, 25 * mm, 20 * mm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    SimpleDocTemplate(str(output), pagesize=A4).build(story)
    return str(output)


def print_voucher(conn: sqlite3.Connection, transaction_id: int, path: str | None = None) -> str:
    """Generate a printable payment/receipt voucher for one transaction."""
    from reportlab.lib.pagesizes import A5
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
    from reportlab.lib.styles import getSampleStyleSheet

    row = conn.execute(
        "SELECT t.*,h.name AS head_name FROM cashbook_transactions t JOIN cashbook_heads h ON h.id=t.head_id WHERE t.id=?",
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Transaction was not found.")
    output = Path(path) if path else Path(REPORTS_DIR) / f"voucher_{row['voucher_no'] or transaction_id}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = "Payment Voucher" if row["txn_type"] == "EXPENSE" else "Receipt Voucher"
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    story.append(Table([
        ["Voucher No", row["voucher_no"] or ""],
        ["Date", row["txn_date"]],
        ["Head", row["head_name"]],
        ["Description", row["description"] or ""],
        ["Payment Method", row["payment_method"]],
        ["Account", row["account_name"]],
        ["Reference", row["reference"] or ""],
        ["Amount", f"{float(row['amount'] or 0):.2f}"],
    ], colWidths=[110, 260]))
    SimpleDocTemplate(str(output), pagesize=A5).build(story)
    return str(output)
