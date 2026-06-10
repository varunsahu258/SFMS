"""PDF receipt generation and reprint tracking for SFMS."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import auth
from audit import log_action
from config import RECEIPTS_DIR, SCHOOL_NAME
from utils import format_currency, now_str

MARGIN = 20 * 72 / 25.4
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
WATERMARK_COLOR = colors.HexColor("#cccccc")


def _settings(conn) -> dict[str, str]:
    """Return application settings as a key/value dictionary."""
    return {str(row[0]): str(row[1] or "") for row in conn.execute("SELECT key, value FROM settings")}


def _receipt_data(conn, receipt_no: str) -> dict:
    """Load a receipt, its student, collector, payment rows, and mode details."""
    receipt = conn.execute(
        """
        SELECT r.*, s.name AS student_name, s.class AS student_class,
               s.section AS student_section, u.username AS printed_by_name
        FROM receipts r
        JOIN students s ON s.id = r.student_id
        LEFT JOIN users u ON u.id = r.printed_by
        WHERE r.receipt_no = ?
        """,
        (receipt_no,),
    ).fetchone()
    if receipt is None:
        raise ValueError(f"Receipt {receipt_no} was not found.")

    columns = [description[0] for description in conn.execute("SELECT r.*, s.name AS student_name, s.class AS student_class, s.section AS student_section, u.username AS printed_by_name FROM receipts r JOIN students s ON s.id = r.student_id LEFT JOIN users u ON u.id = r.printed_by WHERE 0").description]
    receipt_values = dict(zip(columns, receipt)) if not hasattr(receipt, "keys") else dict(receipt)

    payment_columns_available = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
    has_intent = "payment_intent" in payment_columns_available
    has_years = "allocated_academic_year_id" in payment_columns_available and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='academic_years'"
    ).fetchone()
    intent_columns = (
        "p.payment_intent,p.allocated_term,ay.label AS allocated_academic_year,"
        if has_intent and has_years else
        "'REGULAR' AS payment_intent,NULL AS allocated_term,NULL AS allocated_academic_year,"
    )
    year_join = "LEFT JOIN academic_years ay ON ay.id=p.allocated_academic_year_id" if has_intent and has_years else ""
    payment_cursor = conn.execute(
        f"""
        SELECT p.id, p.fee_head_id, COALESCE(l.original_amount,p.amount_due) AS amount_due,
               p.amount_paid, COALESCE(l.balance,0) AS balance,
               p.payment_date, p.payment_mode, p.note, {intent_columns} fh.name AS fee_head,
               u.username AS collected_by_name, ct.cheque_no, ct.bank
        FROM payments p
        LEFT JOIN payment_allocations pa ON pa.payment_id=p.id
        LEFT JOIN charge_ledger l ON l.charge_id=pa.charge_id
        LEFT JOIN fee_heads fh ON fh.id = p.fee_head_id
        {year_join}
        LEFT JOIN users u ON u.id = p.collected_by
        LEFT JOIN cheque_tracker ct ON ct.payment_id = p.id
        WHERE p.receipt_no = ?
        ORDER BY p.id
        """,
        (receipt_no,),
    )
    payment_columns = [description[0] for description in payment_cursor.description]
    payments = [dict(zip(payment_columns, row)) if not hasattr(row, "keys") else dict(row) for row in payment_cursor.fetchall()]
    if not payments:
        raise ValueError(f"Receipt {receipt_no} has no payment rows.")

    receipt_values["payments"] = payments
    return receipt_values


def _fit_text(text: object, max_width: float, font_name: str, font_size: float) -> str:
    """Truncate text so it fits within a fixed-width receipt field."""
    value = str(text or "")
    if stringWidth(value, font_name, font_size) <= max_width:
        return value
    suffix = "..."
    while value and stringWidth(value + suffix, font_name, font_size) > max_width:
        value = value[:-1]
    return value + suffix


def _payment_mode_text(payments: list[dict]) -> str:
    """Return a human-readable summary of all payment modes on the receipt."""
    details: list[str] = []
    for payment in payments:
        mode = str(payment.get("payment_mode") or "").upper()
        if mode == "CHEQUE":
            value = f"Cheque No. {payment.get('cheque_no') or ''}".strip()
        elif mode == "UPI":
            value = f"UPI Ref {payment.get('note') or ''}".strip()
        else:
            value = mode.title() or "Cash"
        if value not in details:
            details.append(value)
    return " / ".join(details)


def _draw_watermark(pdf: canvas.Canvas, page_width: float, page_height: float) -> None:
    """Draw the required centered diagonal duplicate watermark."""
    pdf.saveState()
    pdf.setFillColor(WATERMARK_COLOR)
    pdf.setFont(FONT_BOLD, 60)
    pdf.translate(page_width / 2, page_height / 2)
    pdf.rotate(45)
    pdf.drawCentredString(0, -20, "DUPLICATE")
    pdf.restoreState()


def _draw_logo(pdf: canvas.Canvas, logo_path: str, x: float, y: float, size: float) -> None:
    """Draw the configured logo in grayscale-compatible form when it exists."""
    if not logo_path or not Path(logo_path).is_file():
        return
    pdf.drawImage(ImageReader(logo_path), x, y, width=size, height=size, preserveAspectRatio=True, mask="auto")


def _draw_copy(
    pdf: canvas.Canvas,
    data: dict,
    settings: dict[str, str],
    qr_reader: ImageReader,
    x: float,
    y: float,
    width: float,
    height: float,
    copy_label: str,
) -> None:
    """Draw one school or parent receipt copy inside the supplied rectangle."""
    padding = 10
    left = x + padding
    right = x + width - padding
    top = y + height - padding
    school_name = settings.get("school_name") or SCHOOL_NAME
    logo_path = settings.get("logo_path", "")

    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.8)
    pdf.rect(x, y, width, height)
    _draw_logo(pdf, logo_path, left, top - 38, 36)

    pdf.setFillColor(colors.black)
    pdf.setFont(FONT_BOLD, 12)
    pdf.drawCentredString(x + width / 2, top - 10, _fit_text(school_name, width - 115, FONT_BOLD, 12))
    pdf.setFont(FONT, 8)
    pdf.drawRightString(right, top - 10, copy_label)
    pdf.setFont(FONT_BOLD, 16)
    pdf.drawCentredString(x + width / 2, top - 31, str(data["receipt_no"]))

    student_class = str(data.get("student_class") or "")
    section = str(data.get("student_section") or "")
    class_text = f"{student_class}{f' - {section}' if section else ''}"
    payment_date = str(data["payments"][0].get("payment_date") or "")
    collector = str(data["payments"][0].get("collected_by_name") or data.get("printed_by_name") or "")

    info_y = top - 52
    pdf.setFont(FONT, 9)
    pdf.drawString(left, info_y, _fit_text(f"Student: {data.get('student_name', '')}", width * 0.58, FONT, 9))
    pdf.drawRightString(right, info_y, f"Class: {class_text}")
    pdf.drawString(left, info_y - 14, f"Date: {payment_date}")
    pdf.drawRightString(right, info_y - 14, _fit_text(f"Collected by: {collector}", width * 0.52, FONT, 9))

    table_top = info_y - 31
    col1 = left
    col2 = x + width * 0.61
    col3 = right
    pdf.setFont(FONT_BOLD, 8.5)
    pdf.line(left, table_top + 4, right, table_top + 4)
    pdf.drawString(col1, table_top - 7, "Fee Head")
    pdf.drawRightString(col2 + 25, table_top - 7, "Amount Due")
    pdf.drawRightString(col3, table_top - 7, "Amount Paid")
    pdf.line(left, table_top - 12, right, table_top - 12)

    row_y = table_top - 26
    total_due = 0.0
    total_paid = 0.0
    total_balance = 0.0
    max_rows = max(1, int((height - 155) // 14))
    visible_payments = data["payments"][:max_rows]
    pdf.setFont(FONT, 8.5)
    for payment in visible_payments:
        due = float(payment.get("amount_due") or 0)
        paid = float(payment.get("amount_paid") or 0)
        total_due += due
        total_paid += paid
        total_balance += float(payment.get("balance") or 0)
        pdf.drawString(col1, row_y, _fit_text(payment.get("fee_head") or "Fee", width * 0.46, FONT, 8.5))
        pdf.drawRightString(col2 + 25, row_y, format_currency(due))
        pdf.drawRightString(col3, row_y, format_currency(paid))
        row_y -= 14

    for payment in data["payments"][max_rows:]:
        total_due += float(payment.get("amount_due") or 0)
        total_paid += float(payment.get("amount_paid") or 0)
        total_balance += float(payment.get("balance") or 0)

    pdf.line(left, row_y + 5, right, row_y + 5)
    pdf.setFont(FONT_BOLD, 9)
    pdf.drawString(col1, row_y - 7, "TOTAL")
    pdf.drawRightString(col2 + 25, row_y - 7, format_currency(total_due))
    pdf.drawRightString(col3, row_y - 7, format_currency(total_paid))

    footer_y = y + 28
    pdf.setFont(FONT, 8.5)
    if total_balance > 0:
        pdf.drawString(left, footer_y + 14, f"Balance: {format_currency(total_balance)}")
    advance = next((payment for payment in data["payments"] if payment.get("payment_intent") == "ADVANCE"), None)
    if advance:
        pdf.drawString(left, footer_y + 14, _fit_text(
            f"Allocated: {advance.get('allocated_academic_year') or ''} / {advance.get('allocated_term') or ''}",
            width - 85, FONT, 8.5))
    pdf.drawString(left, footer_y, _fit_text(f"Payment Mode: {_payment_mode_text(data['payments'])}", width - 85, FONT, 8.5))
    pdf.drawImage(qr_reader, right - 48, y + 8, width=42, height=42, preserveAspectRatio=True, mask="auto")


def _open_pdf(pdf_path: str) -> None:
    """Open the generated PDF with the Windows shell when available."""
    if hasattr(os, "startfile"):
        os.startfile(pdf_path)


def _receipt_output_path(conn, receipt_id: int, receipt_no: str, reprint: bool) -> tuple[Path, str]:
    """Return a new immutable filename; existing targets are never reused."""
    if reprint:
        count = conn.execute(
            "SELECT COUNT(*) FROM receipt_print_history WHERE receipt_id=? AND print_type='REPRINT'",
            (receipt_id,),
        ).fetchone()[0]
        filename = f"{receipt_no}_reprint_{int(count) + 1:03d}.pdf"
        print_type = "REPRINT"
    else:
        filename = f"{receipt_no}_original.pdf"
        print_type = "ORIGINAL"
    path = Path(RECEIPTS_DIR) / filename
    if path.exists() or conn.execute(
        "SELECT 1 FROM receipt_print_history WHERE filename=?", (filename,)
    ).fetchone():
        raise FileExistsError(f"Receipt PDF already exists: {path}")
    return path, print_type


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_receipt(conn, receipt_no, reprint=False, reprint_reason: str | None = None):
    """Generate one immutable receipt PDF and append its print-history record."""
    data = _receipt_data(conn, receipt_no)
    settings = _settings(conn)
    receipt_id = int(data["id"])
    total = float(data.get("total_paid") or sum(float(row.get("amount_paid") or 0) for row in data["payments"]))
    payment_date = str(data["payments"][0].get("payment_date") or "")
    qr_payload = f"SFMS|{receipt_no}|{data['student_id']}|{total}|{payment_date}"

    output_dir = Path(RECEIPTS_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_path, print_type = _receipt_output_path(conn, receipt_id, receipt_no, reprint)
    qr_file = tempfile.NamedTemporaryFile(prefix="sfms_qr_", suffix=".png", delete=False)
    qr_file.close()
    pdf_file = tempfile.NamedTemporaryFile(prefix="sfms_receipt_", suffix=".pdf", dir=output_dir, delete=False)
    pdf_file.close()
    temp_pdf_path = Path(pdf_file.name)
    published = False
    try:
        qrcode.make(qr_payload).save(qr_file.name)
        qr_reader = ImageReader(qr_file.name)
        page_width, page_height = A4
        pdf = canvas.Canvas(str(temp_pdf_path), pagesize=A4)
        pdf.setTitle(f"SFMS Receipt {receipt_no}")
        pdf.setAuthor(settings.get("school_name") or SCHOOL_NAME)
        pdf.setSubject(f"{print_type}:{target_path.name}")

        if reprint:
            _draw_watermark(pdf, page_width, page_height)

        if str(data.get("receipt_type") or "BIG").upper() == "SMALL":
            panel_y = page_height / 2
            panel_height = page_height / 2 - MARGIN
            available_width = page_width - 2 * MARGIN
            panel_width = available_width / 2
            _draw_copy(pdf, data, settings, qr_reader, MARGIN, panel_y, panel_width, panel_height, "SCHOOL COPY")
            _draw_copy(pdf, data, settings, qr_reader, MARGIN + panel_width, panel_y, panel_width, panel_height, "PARENT COPY")
        else:
            half_height = page_height / 2
            _draw_copy(pdf, data, settings, qr_reader, MARGIN, half_height + 8, page_width - 2 * MARGIN, half_height - MARGIN - 8, "SCHOOL COPY")
            pdf.saveState()
            pdf.setDash(2, 3)
            pdf.line(MARGIN, half_height, page_width - MARGIN, half_height)
            pdf.restoreState()
            _draw_copy(pdf, data, settings, qr_reader, MARGIN, MARGIN, page_width - 2 * MARGIN, half_height - MARGIN - 8, "PARENT COPY")

        pdf.showPage()
        pdf.save()
        # Hard-link publication is atomic and fails if the immutable target exists.
        os.link(temp_pdf_path, target_path)
        published = True
        file_hash = _file_sha256(target_path)
        user_id = auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION is not None else data.get("printed_by")
        timestamp = now_str()
        try:
            conn.execute(
                """INSERT INTO receipt_print_history(
                       receipt_id,print_type,filename,file_sha256,printed_at,printed_by
                   ) VALUES(?,?,?,?,?,?)""",
                (receipt_id, print_type, target_path.name, file_hash, timestamp, user_id),
            )
            if reprint:
                conn.execute(
                    """UPDATE receipts SET reprint_count=COALESCE(reprint_count,0)+1,
                           last_reprint_at=?,last_reprint_by=? WHERE id=?""",
                    (timestamp, user_id, receipt_id),
                )
                updated = conn.execute(
                    "SELECT reprint_count FROM receipts WHERE id=?", (receipt_id,)
                ).fetchone()
                reprint_count = int(updated[0] or 0) if updated else 0
                log_action(
                    conn, user_id, "RECEIPT_REPRINT", "receipts", receipt_id, None,
                    {
                        "receipt_id": receipt_id,
                        "receipt_no": receipt_no,
                        "reprint_count": reprint_count,
                        "reprinted_by": user_id,
                        "reprinted_at": timestamp,
                        "reason": reprint_reason or "",
                        "filename": target_path.name,
                    },
                )
            conn.commit()
        except Exception:
            conn.rollback()
            target_path.unlink(missing_ok=True)
            published = False
            raise
    except Exception:
        if published:
            target_path.unlink(missing_ok=True)
            published = False
        raise
    finally:
        Path(qr_file.name).unlink(missing_ok=True)
        temp_pdf_path.unlink(missing_ok=True)

    _open_pdf(str(target_path))
    return str(target_path)
