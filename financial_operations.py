"""Transactional financial persistence isolated from Tkinter presentation."""

from __future__ import annotations

from audit import log_financial_action
from ledger import allocate_payment
from receipt_integrity import sign_receipt
from utils import generate_receipt_no, now_str, today_str


def record_collection(conn, student_id: int, receipt_type: str, user_id: int,
                      items: list[dict]) -> dict:
    """Commit payment/allocation/receipt/audit rows atomically; never print here."""
    receipt_no = generate_receipt_no(conn)
    payment_date = today_str()
    payment_ids = []
    total = sum(item["amount_paying"] for item in items)
    with conn:
        for item in items:
            cursor = conn.execute(
                """INSERT INTO payments(
                       student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,
                       payment_date,collected_by,payment_mode,note,hash,cheque_number,
                       upi_reference,cheque_status,payment_intent,
                       allocated_academic_year_id,allocated_term)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'REGULAR',
                          (SELECT id FROM academic_years WHERE label=?),?)""",
                (student_id, receipt_no, item["fee_head_id"], str(item["amount_due"]),
                 str(item["amount_paying"]), str(item.get("balance_after", 0)),
                 payment_date, user_id, item["mode"], item.get("note", ""), "",
                 item.get("cheque_no") if item["mode"] == "CHEQUE" else None,
                 (item.get("upi_reference") or item.get("note")) if item["mode"] == "UPI" else None,
                 "PENDING" if item["mode"] == "CHEQUE" else None,
                 item["academic_year"], item.get("due_date")),
            )
            payment_ids.append(cursor.lastrowid)
            allocations = item.get("allocations") or [{
                "charge_id": item["charge_id"], "amount": item["amount_paying"]
            }]
            for allocation in allocations:
                allocate_payment(
                    conn, cursor.lastrowid, allocation["charge_id"],
                    str(allocation["amount"]), "PAYMENT",
                )
            if item["mode"] == "CHEQUE":
                conn.execute(
                    """INSERT INTO cheque_tracker(payment_id,cheque_no,bank,amount,
                              collected_on,status,updated_at)
                       VALUES(?,?,?,?,?,'PENDING',?)""",
                    (cursor.lastrowid, item["cheque_no"], item["bank"],
                     str(item["amount_paying"]), payment_date, now_str()),
                )
        receipt = conn.execute(
            """INSERT INTO receipts(receipt_no,student_id,total_paid,receipt_type,
                       printed_at,printed_by,reprint_count) VALUES(?,?,?,?,?,?,0)""",
            (receipt_no, student_id, str(total), receipt_type, now_str(), user_id),
        )
        sign_receipt(conn, receipt.lastrowid)
        log_financial_action(
            conn, "PAYMENT_COLLECTED", user_id,
            {"table": "payments", "record_id": receipt_no,
             "receipt_no": receipt_no, "payment_ids": payment_ids,
             "total_paid": str(total)},
        )
    return {"receipt_id": int(receipt.lastrowid), "receipt_no": receipt_no,
            "payment_ids": payment_ids, "total_paid": total}


def record_advance_payment(conn, student_id: int, charge_id: int, fee_head_id: int,
                           amount, academic_year_id: int, academic_year: str,
                           term: str, user_id: int) -> dict:
    """Persist a term/year-scoped advance and its formal receipt atomically."""
    receipt_no = generate_receipt_no(conn)
    with conn:
        payment = conn.execute(
            """INSERT INTO payments(student_id,receipt_no,fee_head_id,amount_due,
                       amount_paid,balance,payment_date,collected_by,payment_mode,note,hash,
                       payment_intent,allocated_academic_year_id,allocated_term)
               VALUES(?,?,?,?,?,?,?,?,'CASH',?,?,'ADVANCE',?,?)""",
            (student_id, receipt_no, fee_head_id, 0, str(amount), 0, today_str(), user_id,
             f"ADVANCE | Year: {academic_year} | Term: {term}", "",
             academic_year_id, term),
        )
        allocate_payment(conn, payment.lastrowid, charge_id, str(amount), "ADVANCE")
        receipt = conn.execute(
            """INSERT INTO receipts(receipt_no,student_id,total_paid,receipt_type,
                       printed_at,printed_by,reprint_count)
               VALUES(?,?,?,'ADVANCE RECEIPT',?,?,0)""",
            (receipt_no, student_id, str(amount), now_str(), user_id),
        )
        sign_receipt(conn, receipt.lastrowid)
        log_financial_action(
            conn, "ADVANCE_PAYMENT", user_id,
            {"table": "payments", "record_id": payment.lastrowid,
             "receipt_no": receipt_no, "amount": str(amount),
             "academic_year": academic_year, "term": term},
        )
    return {"receipt_id": int(receipt.lastrowid), "receipt_no": receipt_no,
            "payment_id": int(payment.lastrowid)}
