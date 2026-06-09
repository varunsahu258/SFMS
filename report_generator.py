"""Black-and-white PDF report generation for SFMS."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config import REPORTS_DIR, SCHOOL_NAME
from utils import format_currency, today_str

MARGIN = 20 * mm
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _row_dicts(cursor) -> list[dict]:
    """Convert cursor rows to dictionaries regardless of row factory configuration."""
    columns = [description[0] for description in cursor.description]
    return [dict(row) if hasattr(row, "keys") else dict(zip(columns, row)) for row in cursor.fetchall()]


def _settings(conn) -> dict[str, str]:
    """Return settings as a string dictionary."""
    return {str(row[0]): str(row[1] or "") for row in conn.execute("SELECT key, value FROM settings")}


def _school_name(conn) -> str:
    """Return the configured school name with the project default as fallback."""
    return _settings(conn).get("school_name") or SCHOOL_NAME


def _styles():
    """Build Helvetica-only paragraph styles for reports."""
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("SFMS Title", parent=styles["Title"], fontName=FONT_BOLD, fontSize=16, leading=19, alignment=TA_CENTER, textColor=colors.black),
        "subtitle": ParagraphStyle("SFMS Subtitle", parent=styles["Heading2"], fontName=FONT_BOLD, fontSize=12, leading=15, alignment=TA_CENTER, textColor=colors.black),
        "heading": ParagraphStyle("SFMS Heading", parent=styles["Heading3"], fontName=FONT_BOLD, fontSize=10, leading=13, textColor=colors.black, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("SFMS Body", parent=styles["BodyText"], fontName=FONT, fontSize=8, leading=10, textColor=colors.black),
        "small": ParagraphStyle("SFMS Small", parent=styles["BodyText"], fontName=FONT, fontSize=7, leading=8, textColor=colors.black),
        "right": ParagraphStyle("SFMS Right", parent=styles["BodyText"], fontName=FONT_BOLD, fontSize=9, leading=11, alignment=TA_RIGHT, textColor=colors.black),
    }


def _document(path: str, title: str) -> SimpleDocTemplate:
    """Create an A4 report document with required 20 mm margins."""
    return SimpleDocTemplate(
        path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=title,
        author="SFMS",
    )


def _header(story: list, conn, title: str) -> None:
    """Append the standard school, optional logo, and report title header."""
    styles = _styles()
    settings = _settings(conn)
    logo_path = settings.get("logo_path", "")
    title_block = [
        Paragraph(settings.get("school_name") or SCHOOL_NAME, styles["title"]),
        Paragraph(title, styles["subtitle"]),
    ]
    if logo_path and Path(logo_path).is_file():
        logo = Image(logo_path, width=18 * mm, height=18 * mm, kind="proportional")
        header = Table([[logo, title_block]], colWidths=[22 * mm, 148 * mm])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header)
    else:
        story.extend(title_block)
    story.append(Spacer(1, 5 * mm))


def _table(data: list[list], widths=None, font_size: float = 7.5, repeat_rows: int = 1, right_columns: Iterable[int] = ()) -> Table:
    """Create a laser-printer-safe black-and-white table."""
    table = Table(data, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    commands = [
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 2),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for column in right_columns:
        commands.append(("ALIGN", (column, 1), (column, -1), "RIGHT"))
    table.setStyle(TableStyle(commands))
    return table


def _output_path(filename: str) -> str:
    """Return a report path after ensuring the output directory exists."""
    Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)
    return str(Path(REPORTS_DIR) / filename)


def _parse_date(value: str | None) -> date | None:
    """Parse supported SFMS date text."""
    if not value:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(str(value), date_format).date()
        except ValueError:
            continue
    return None


def _academic_bounds(conn, academic_year: str) -> tuple[date | None, date | None]:
    """Return stored or inferred start/end dates for an academic year."""
    row = conn.execute("SELECT start_date, end_date FROM academic_years WHERE label = ?", (academic_year,)).fetchone()
    if row:
        start = _parse_date(row[0])
        end = _parse_date(row[1])
        if start and end:
            return start, end
    try:
        start_year = int(str(academic_year).split("-")[0])
    except (TypeError, ValueError):
        return None, None
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def _in_bounds(value: str | None, bounds: tuple[date | None, date | None]) -> bool:
    """Return whether a payment date falls in an academic-year range."""
    parsed = _parse_date(value)
    start, end = bounds
    return bool(parsed and start and end and start <= parsed <= end)


def _safe_name(value: object) -> str:
    """Return a filesystem-safe identifier."""
    return "".join(character for character in str(value) if character.isalnum() or character in ("-", "_")) or "report"


def daily_report(conn, date_str) -> str:
    """Generate receipt, fee-head, staff, and grand-total collections for a date."""
    rows = _row_dicts(conn.execute(
        """
        SELECT p.receipt_no, p.payment_date, p.amount_paid, p.payment_mode, p.note,
               s.name AS student, fh.name AS fee_head, u.username AS collected_by,
               ct.cheque_no
        FROM payments p
        JOIN students s ON s.id = p.student_id
        LEFT JOIN fee_heads fh ON fh.id = p.fee_head_id
        LEFT JOIN users u ON u.id = p.collected_by
        LEFT JOIN cheque_tracker ct ON ct.payment_id = p.id
        WHERE p.payment_date = ?
        ORDER BY p.receipt_no, p.id
        """,
        (date_str,),
    ))

    receipt_groups: dict[str, dict] = {}
    head_totals: defaultdict[str, float] = defaultdict(float)
    staff_totals: defaultdict[str, float] = defaultdict(float)
    grand_total = 0.0
    for row in rows:
        group = receipt_groups.setdefault(row["receipt_no"], {"student": row["student"], "heads": [], "amounts": [], "modes": [], "staff": row.get("collected_by") or ""})
        group["heads"].append(row.get("fee_head") or "Fee")
        group["amounts"].append(format_currency(row.get("amount_paid") or 0))
        mode = str(row.get("payment_mode") or "").upper()
        mode_text = f"Cheque {row.get('cheque_no') or ''}" if mode == "CHEQUE" else (f"UPI {row.get('note') or ''}" if mode == "UPI" else mode.title())
        if mode_text and mode_text not in group["modes"]:
            group["modes"].append(mode_text)
        amount = float(row.get("amount_paid") or 0)
        head_totals[row.get("fee_head") or "Fee"] += amount
        staff_totals[row.get("collected_by") or "Unknown"] += amount
        grand_total += amount

    path = _output_path(f"daily_{str(date_str).replace('-', '').replace('/', '')}.pdf")
    story: list = []
    _header(story, conn, f"Daily Collection Report — {date_str}")
    receipt_data = [["Receipt No", "Student", "Fee Heads", "Amounts", "Mode", "Collected By"]]
    for receipt_no, group in receipt_groups.items():
        receipt_data.append([receipt_no, group["student"], ", ".join(group["heads"]), ", ".join(group["amounts"]), " / ".join(group["modes"]), group["staff"]])
    if len(receipt_data) == 1:
        receipt_data.append(["No collections", "", "", "", "", ""])
    story.append(_table(receipt_data, [25 * mm, 29 * mm, 36 * mm, 27 * mm, 26 * mm, 27 * mm], 6.5, right_columns=(3,)))

    styles = _styles()
    story.extend([Paragraph("Fee Head Totals", styles["heading"]), _table([["Fee Head", "Total Collected"]] + [[name, format_currency(total)] for name, total in sorted(head_totals.items())] or [["Fee Head", "Total Collected"], ["No collections", format_currency(0)]], [90 * mm, 80 * mm], right_columns=(1,))])
    story.extend([Paragraph("Staff Totals", styles["heading"]), _table([["Staff Name", "Total Collected"]] + [[name, format_currency(total)] for name, total in sorted(staff_totals.items())] or [["Staff Name", "Total Collected"], ["No collections", format_currency(0)]], [90 * mm, 80 * mm], right_columns=(1,))])
    story.extend([Spacer(1, 4 * mm), Paragraph(f"Grand Total: {format_currency(grand_total)}", styles["right"])])
    _document(path, f"Daily Collection Report {date_str}").build(story)
    return path


def monthly_report(conn, year, month) -> str:
    """Generate date-grouped monthly collections with previous-month comparison."""
    year = int(year)
    month = int(month)
    rows = _row_dicts(conn.execute("SELECT receipt_no, payment_date, amount_paid FROM payments ORDER BY payment_date, receipt_no"))
    totals: defaultdict[str, float] = defaultdict(float)
    receipts: defaultdict[str, set[str]] = defaultdict(set)
    current_total = 0.0
    previous_total = 0.0
    previous_year = year if month > 1 else year - 1
    previous_month = month - 1 if month > 1 else 12
    for row in rows:
        parsed = _parse_date(row.get("payment_date"))
        if not parsed:
            continue
        amount = float(row.get("amount_paid") or 0)
        if parsed.year == year and parsed.month == month:
            key = parsed.strftime("%d-%m-%Y")
            totals[key] += amount
            receipts[key].add(str(row.get("receipt_no") or ""))
            current_total += amount
        elif parsed.year == previous_year and parsed.month == previous_month:
            previous_total += amount

    month_name = datetime(year, month, 1).strftime("%B %Y")
    path = _output_path(f"monthly_{year}_{month:02d}.pdf")
    story: list = []
    _header(story, conn, f"Monthly Collection Report — {month_name}")
    data = [["Date", "Receipt Count", "Total Collected"]]
    for day in sorted(totals, key=lambda value: datetime.strptime(value, "%d-%m-%Y")):
        data.append([day, len(receipts[day]), format_currency(totals[day])])
    if len(data) == 1:
        data.append(["No collections", 0, format_currency(0)])
    story.append(_table(data, [62 * mm, 43 * mm, 65 * mm], right_columns=(1, 2)))
    styles = _styles()
    story.extend([Spacer(1, 6 * mm), Paragraph(f"Previous Month: {format_currency(previous_total)} | This Month: {format_currency(current_total)}", styles["right"])])
    _document(path, f"Monthly Collection Report {month_name}").build(story)
    return path


def classwise_dues_report(conn, class_name, academic_year) -> str:
    """Generate outstanding fee-head balances for every student in a class."""
    rows = _row_dicts(conn.execute(
        """
        SELECT s.id AS student_id, s.name AS student, fh.name AS fee_head,
               fs.amount AS amount_due, fs.due_date,
               COALESCE(SUM(p.amount_paid), 0) AS paid,
               fs.amount - COALESCE(SUM(p.amount_paid), 0) AS balance
        FROM students s
        JOIN fee_structure fs ON fs.class = s.class AND fs.academic_year = ?
        JOIN fee_heads fh ON fh.id = fs.fee_head_id
        LEFT JOIN payments p ON p.student_id = s.id AND p.fee_head_id = fs.fee_head_id
        WHERE s.class = ? AND s.is_active = 1
        GROUP BY s.id, fs.id
        HAVING balance > 0
        ORDER BY s.name, fh.name
        """,
        (academic_year, class_name),
    ))
    today = date.today()
    data = [["Student", "Fee Head", "Amount Due", "Paid", "Balance", "Days Overdue"]]
    for row in rows:
        due_date = _parse_date(row.get("due_date"))
        days = max(0, (today - due_date).days) if due_date else 0
        data.append([row["student"], row["fee_head"], format_currency(row["amount_due"] or 0), format_currency(row["paid"] or 0), format_currency(row["balance"] or 0), days])
    if len(data) == 1:
        data.append(["No outstanding dues", "", format_currency(0), format_currency(0), format_currency(0), 0])
    path = _output_path(f"class_dues_{_safe_name(class_name)}_{_safe_name(academic_year)}.pdf")
    story: list = []
    _header(story, conn, f"Classwise Dues Report — {class_name} ({academic_year})")
    story.append(_table(data, [36 * mm, 36 * mm, 27 * mm, 24 * mm, 27 * mm, 20 * mm], 6.8, right_columns=(2, 3, 4, 5)))
    _document(path, f"Classwise Dues {class_name}").build(story)
    return path


def defaulter_report(conn, days_threshold) -> str:
    """Generate students with positive old payment balances, highest balance first."""
    threshold = int(days_threshold)
    cutoff = date.today() - timedelta(days=threshold)
    rows = _row_dicts(conn.execute(
        """
        SELECT s.id, s.name AS student, s.class, p.payment_date, p.balance, fh.name AS fee_head
        FROM payments p
        JOIN students s ON s.id = p.student_id
        LEFT JOIN fee_heads fh ON fh.id = p.fee_head_id
        WHERE p.balance > 0
        ORDER BY s.name, p.payment_date
        """
    ))
    groups: dict[int, dict] = {}
    for row in rows:
        payment_date = _parse_date(row.get("payment_date"))
        if not payment_date or payment_date >= cutoff:
            continue
        group = groups.setdefault(row["id"], {"student": row["student"], "class": row.get("class") or "", "last_payment": payment_date, "heads": [], "balance": 0.0})
        group["last_payment"] = max(group["last_payment"], payment_date)
        group["heads"].append(row.get("fee_head") or "Fee")
        group["balance"] += float(row.get("balance") or 0)
    ordered = sorted(groups.values(), key=lambda item: item["balance"], reverse=True)
    data = [["Student", "Class", "Fee Heads", "Last Payment", "Days", "Total Balance"]]
    for group in ordered:
        data.append([group["student"], group["class"], ", ".join(sorted(set(group["heads"]))), group["last_payment"].strftime("%d-%m-%Y"), (date.today() - group["last_payment"]).days, format_currency(group["balance"])])
    if len(data) == 1:
        data.append(["No defaulters", "", "", "", "", format_currency(0)])
    path = _output_path(f"defaulters_{threshold}_days.pdf")
    story: list = []
    _header(story, conn, f"Defaulter Report — Over {threshold} Days")
    story.append(_table(data, [33 * mm, 22 * mm, 43 * mm, 27 * mm, 16 * mm, 29 * mm], 6.8, right_columns=(4, 5)))
    _document(path, f"Defaulter Report {threshold} Days").build(story)
    return path


def ytd_report(conn, academic_year) -> str:
    """Generate expected, collected, and outstanding totals per class."""
    expected_rows = _row_dicts(conn.execute(
        """
        SELECT fs.class, SUM(fs.amount) AS fee_total,
               (SELECT COUNT(*) FROM students s WHERE s.class = fs.class AND s.is_active = 1) AS student_count
        FROM fee_structure fs
        WHERE fs.academic_year = ?
        GROUP BY fs.class
        ORDER BY fs.class
        """,
        (academic_year,),
    ))
    bounds = _academic_bounds(conn, academic_year)
    payment_rows = _row_dicts(conn.execute(
        """
        SELECT s.class, p.amount_paid, p.payment_date
        FROM payments p JOIN students s ON s.id = p.student_id
        """
    ))
    collected: defaultdict[str, float] = defaultdict(float)
    for row in payment_rows:
        if _in_bounds(row.get("payment_date"), bounds):
            collected[row.get("class") or ""] += float(row.get("amount_paid") or 0)
    data = [["Class", "Students", "Expected", "Collected", "Outstanding"]]
    expected_total = collected_total = 0.0
    for row in expected_rows:
        expected = float(row.get("fee_total") or 0) * int(row.get("student_count") or 0)
        class_collected = collected[row.get("class") or ""]
        outstanding = expected - class_collected
        data.append([row.get("class") or "", row.get("student_count") or 0, format_currency(expected), format_currency(class_collected), format_currency(outstanding)])
        expected_total += expected
        collected_total += class_collected
    data.append(["TOTAL", "", format_currency(expected_total), format_currency(collected_total), format_currency(expected_total - collected_total)])
    path = _output_path(f"ytd_{_safe_name(academic_year)}.pdf")
    story: list = []
    _header(story, conn, f"Year-to-Date Report — {academic_year}")
    story.append(_table(data, [38 * mm, 24 * mm, 37 * mm, 35 * mm, 36 * mm], right_columns=(1, 2, 3, 4)))
    _document(path, f"YTD Report {academic_year}").build(story)
    return path


def cashflow_chart_report(conn, academic_year) -> str:
    """Generate a black-and-white monthly collection bar chart."""
    bounds = _academic_bounds(conn, academic_year)
    rows = _row_dicts(conn.execute("SELECT payment_date, amount_paid FROM payments"))
    monthly: defaultdict[tuple[int, int], float] = defaultdict(float)
    for row in rows:
        parsed = _parse_date(row.get("payment_date"))
        if parsed and _in_bounds(row.get("payment_date"), bounds):
            monthly[(parsed.year, parsed.month)] += float(row.get("amount_paid") or 0)
    start, end = bounds
    keys: list[tuple[int, int]] = []
    if start and end:
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            keys.append((cursor.year, cursor.month))
            cursor = date(cursor.year + (1 if cursor.month == 12 else 0), 1 if cursor.month == 12 else cursor.month + 1, 1)
    labels = [datetime(year, month, 1).strftime("%b") for year, month in keys]
    values = [monthly[key] for key in keys]

    drawing = Drawing(470, 280)
    chart = VerticalBarChart()
    chart.x = 45
    chart.y = 45
    chart.height = 195
    chart.width = 400
    chart.data = [values or [0]]
    chart.categoryAxis.categoryNames = labels or ["N/A"]
    chart.categoryAxis.labels.fontName = FONT
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = FONT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(values or [1]) * 1.1 or 1
    chart.valueAxis.valueStep = max(1, chart.valueAxis.valueMax / 5)
    chart.bars[0].fillColor = colors.HexColor("#777777")
    chart.bars[0].strokeColor = colors.black
    drawing.add(chart)
    drawing.add(String(235, 10, "Month", fontName=FONT, fontSize=8, textAnchor="middle"))
    drawing.add(String(8, 150, "Rs. collected", fontName=FONT, fontSize=8))

    path = _output_path(f"cashflow_{_safe_name(academic_year)}.pdf")
    story: list = []
    _header(story, conn, f"Cashflow Chart — {academic_year}")
    story.append(drawing)
    story.append(Spacer(1, 4 * mm))
    story.append(_table([["Month", "Collected"]] + [[label, format_currency(value)] for label, value in zip(labels, values)], [85 * mm, 85 * mm], right_columns=(1,)))
    _document(path, f"Cashflow Chart {academic_year}").build(story)
    return path


def audit_export(conn, filters=None) -> str:
    """Export the full or filtered immutable audit log."""
    filters = filters or {}
    clauses: list[str] = []
    params: list[object] = []
    allowed = {"user_id": "a.user_id", "action": "a.action", "table_name": "a.table_name", "tamper_attempt": "a.tamper_attempt"}
    for key, column in allowed.items():
        value = filters.get(key)
        if value not in (None, ""):
            clauses.append(f"{column} = ?")
            params.append(value)
    sql = """
        SELECT a.id, a.timestamp, COALESCE(u.username, '') AS username, a.action,
               a.table_name, a.record_id, a.old_value, a.new_value, a.tamper_attempt
        FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY a.id"
    rows = _row_dicts(conn.execute(sql, params))
    date_from = _parse_date(filters.get("date_from"))
    date_to = _parse_date(filters.get("date_to"))
    if date_from or date_to:
        rows = [row for row in rows if (parsed := _parse_date(str(row.get("timestamp") or "").split(" ")[0])) and (date_from is None or parsed >= date_from) and (date_to is None or parsed <= date_to)]

    styles = _styles()
    data = [["ID", "Timestamp", "User", "Action", "Table", "Record", "Old / New", "Tamper"]]
    for row in rows:
        values = f"Old: {row.get('old_value') or ''}\nNew: {row.get('new_value') or ''}"
        data.append([
            row["id"], row.get("timestamp") or "", row.get("username") or "", row.get("action") or "",
            row.get("table_name") or "", row.get("record_id") or "",
            Paragraph(str(values).replace("\n", "<br/>"), styles["small"]), "Yes" if row.get("tamper_attempt") else "No",
        ])
    if len(data) == 1:
        data.append(["", "No audit records", "", "", "", "", "", ""])
    path = _output_path(f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    story: list = []
    _header(story, conn, "Audit Log Export")
    story.append(_table(data, [9 * mm, 25 * mm, 18 * mm, 25 * mm, 21 * mm, 16 * mm, 44 * mm, 12 * mm], 5.8))
    _document(path, "Audit Log Export").build(story)
    return path


def fee_notice_pdf(conn, class_name) -> str:
    """Generate one black-and-white A4 fee notice per student with dues."""
    rows = _row_dicts(conn.execute(
        """
        SELECT s.id AS student_id, s.name AS student, s.class,
               fh.name AS fee_head, fs.amount AS amount_due, fs.due_date,
               COALESCE(SUM(p.amount_paid), 0) AS paid,
               fs.amount - COALESCE(SUM(p.amount_paid), 0) AS balance
        FROM students s
        JOIN fee_structure fs ON fs.class = s.class
        JOIN fee_heads fh ON fh.id = fs.fee_head_id
        LEFT JOIN payments p ON p.student_id = s.id AND p.fee_head_id = fs.fee_head_id
        WHERE s.class = ? AND s.is_active = 1
        GROUP BY s.id, fs.id
        HAVING balance > 0
        ORDER BY s.name, fh.name
        """,
        (class_name,),
    ))
    students: dict[int, dict] = {}
    for row in rows:
        student = students.setdefault(
            row["student_id"],
            {"name": row["student"], "class": row["class"], "items": []},
        )
        student["items"].append(row)
    if not students:
        raise ValueError(f"No outstanding dues found for {class_name}.")

    path = _output_path(
        f"fee_notices_{_safe_name(class_name)}_{datetime.now().strftime('%d%m%Y')}.pdf"
    )
    styles = _styles()
    story: list = []
    for index, student in enumerate(students.values()):
        if index:
            story.append(PageBreak())
        _header(story, conn, "FEE DUE NOTICE")
        story.extend([
            Paragraph(f"Student: <b>{student['name']}</b>", styles["body"]),
            Paragraph(f"Class: <b>{student['class']}</b>", styles["body"]),
            Spacer(1, 6 * mm),
            Paragraph("The following school fees remain outstanding:", styles["body"]),
            Spacer(1, 3 * mm),
        ])
        data = [["Fee Head", "Amount Due", "Paid", "Balance", "Due Date"]]
        total = 0.0
        due_dates: list[date] = []
        for item in student["items"]:
            balance = float(item["balance"] or 0)
            total += balance
            parsed_due = _parse_date(item.get("due_date"))
            if parsed_due:
                due_dates.append(parsed_due)
            data.append([
                item["fee_head"], format_currency(item["amount_due"] or 0),
                format_currency(item["paid"] or 0), format_currency(balance),
                item.get("due_date") or "",
            ])
        data.append(["TOTAL", "", "", format_currency(total), ""])
        story.append(_table(data, [55 * mm, 32 * mm, 28 * mm, 32 * mm, 23 * mm], 7.5, right_columns=(1, 2, 3)))
        deadline = min(due_dates).strftime("%d-%m-%Y") if due_dates else today_str()
        story.extend([
            Spacer(1, 12 * mm),
            Paragraph(f"Please clear the above dues by {deadline}.", styles["body"]),
            Spacer(1, 20 * mm),
            Paragraph("Authorized Signatory", styles["right"]),
        ])
    _document(path, f"Fee Notices {class_name}").build(story)
    return path
