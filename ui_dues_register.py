"""Chronological student financial register."""

from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import ttk

import auth
from ledger import active_academic_year, ensure_student_charges
from ledger_service import LedgerService
from ui_master_utils import connect_db
from ui_workspace import WorkspacePage
from utils import format_currency


def _date_key(value: str | None) -> datetime:
    """Parse every date format stored by current and legacy SFMS versions."""
    text = str(value or "").strip()
    for fmt in (
        "%d-%m-%Y %H:%M:%S.%f", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.min


def _event_time(display_date: str | None, created_at: str | None = None) -> datetime:
    """Use the transaction date plus its creation time for stable chronology."""
    displayed = _date_key(display_date)
    created = _date_key(created_at)
    if displayed == datetime.min:
        return created
    if created != datetime.min and displayed.time() == datetime.min.time():
        return displayed.replace(hour=created.hour, minute=created.minute, second=created.second,
                                 microsecond=created.microsecond)
    return displayed


def _display_event_date(display_date: str | None, created_at: str | None = None) -> str:
    """Show same-day creation time when the stored transaction date has no time."""
    displayed = _date_key(display_date)
    created = _date_key(created_at)
    if displayed != datetime.min and displayed.time() == datetime.min.time() and created != datetime.min:
        return displayed.replace(hour=created.hour, minute=created.minute, second=created.second,
                                 microsecond=created.microsecond).strftime("%d-%m-%Y %H:%M:%S")
    return str(display_date or created_at or "")


def _has_column(conn, table: str, column: str) -> bool:
    """Return whether a current or legacy table exposes a column."""
    return column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def student_dues_register(conn, student_id: int) -> dict:
    """Return student details, totals, and immutable financial events chronologically."""
    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if student is None:
        raise ValueError("Student was not found.")
    year = active_academic_year(conn)
    if year:
        ensure_student_charges(conn, year, student_id)
    events: list[dict] = []
    charges = conn.execute(
        """SELECT c.id,c.academic_year,c.original_amount,c.due_date,c.created_at,c.status,fh.name fee_head
           FROM student_charges c JOIN fee_heads fh ON fh.id=c.fee_head_id
           WHERE c.student_id=? ORDER BY c.id""", (student_id,),
    ).fetchall()
    for row in charges:
        event_date = row["created_at"] or row["due_date"] or ""
        events.append({"date": event_date, "academic_year": row["academic_year"], "type": "CHARGE",
                       "reference": f"Charge #{row['id']}", "description": f"{row['fee_head']} (due {row['due_date'] or 'not set'})",
                       "debit": float(row["original_amount"] or 0), "credit": 0.0,
                       "_sort_at": _event_time(event_date, row["created_at"]), "_sequence": (0, int(row["id"]))})
    adjustments = conn.execute(
        """SELECT a.id,a.amount,a.adjustment_type,a.reason,a.created_at,c.academic_year,fh.name fee_head
           FROM charge_adjustments a JOIN student_charges c ON c.id=a.charge_id
           JOIN fee_heads fh ON fh.id=c.fee_head_id WHERE c.student_id=? ORDER BY a.id""", (student_id,),
    ).fetchall()
    for row in adjustments:
        event_date = row["created_at"] or ""
        events.append({"date": event_date, "academic_year": row["academic_year"], "type": row["adjustment_type"],
                       "reference": f"Adjustment #{row['id']}",
                       "description": f"{row['fee_head']}: {row['reason'] or row['adjustment_type'].title()}",
                       "debit": 0.0, "credit": float(row["amount"] or 0),
                       "_sort_at": _event_time(event_date, row["created_at"]), "_sequence": (1, int(row["id"]))})
    allocation_timestamp = "a.created_at" if _has_column(conn, "payment_allocations", "created_at") else "NULL"
    payments = conn.execute(
        f"""SELECT p.id,a.id allocation_id,p.receipt_no,p.payment_date,p.payment_mode,p.note,a.amount_allocated,
                   a.allocation_type,c.academic_year,fh.name fee_head,{allocation_timestamp} allocation_created_at
            FROM payment_allocations a JOIN payments p ON p.id=a.payment_id
            JOIN student_charges c ON c.id=a.charge_id JOIN fee_heads fh ON fh.id=c.fee_head_id
            WHERE p.student_id=? ORDER BY p.id,a.id""", (student_id,),
    ).fetchall()
    for row in payments:
        reversal = row["allocation_type"] == "REVERSAL"
        amount = float(row["amount_allocated"] or 0)
        event_date = row["payment_date"] or row["allocation_created_at"] or ""
        display_date = _display_event_date(event_date, row["allocation_created_at"])
        events.append({"date": display_date, "academic_year": row["academic_year"],
                       "type": "VOID/REVERSAL" if reversal else "PAYMENT",
                       "reference": row["receipt_no"] or f"Payment #{row['id']}",
                       "description": f"{row['fee_head']} • {row['payment_mode'] or ''}{' • ' + row['note'] if row['note'] else ''}",
                       "debit": amount if reversal else 0.0, "credit": 0.0 if reversal else amount,
                       "_sort_at": _event_time(event_date, row["allocation_created_at"]),
                       "_sequence": (2, int(row["id"]), int(row["allocation_id"]))})
    events.sort(key=lambda event: (event["_sort_at"], event["_sequence"]))
    for event in events:
        event.pop("_sort_at", None)
        event.pop("_sequence", None)
    totals = {
        "charged": sum(event["debit"] for event in events if event["type"] == "CHARGE"),
        "paid": sum(event["credit"] for event in events if event["type"] == "PAYMENT"),
        "adjustments": sum(event["credit"] for event in events if event["type"] in {"DISCOUNT", "EXEMPTION"}),
        "reversed": sum(event["debit"] for event in events if event["type"] == "VOID/REVERSAL"),
        "outstanding": LedgerService(conn).get_outstanding(student_id),
    }
    return {"student": dict(student), "academic_year": year, "events": events, "totals": totals}


class DuesRegisterWindow(WorkspacePage):
    """Search a student and inspect their complete chronological dues register."""

    @auth.require_permission("view_dues")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
        self.title("Dues Register"); self.geometry("1200x700")
        self.search_var = tk.StringVar(); self._build(); self.search()

    def _build(self) -> None:
        page = ttk.Frame(self, padding=20); page.pack(fill="both", expand=True)
        ttk.Label(page, text="Student Dues Register", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="Charges, receipts, discounts, exemptions and reversals in chronological order.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        search = ttk.Frame(page); search.pack(fill="x")
        entry = ttk.Entry(search, textvariable=self.search_var, width=44); entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda _event: self.search())
        ttk.Button(search, text="Search", command=self.search, style="Accent.TButton").pack(side="left", padx=8)
        student_frame = ttk.Frame(page)
        student_frame.pack(fill="x", pady=(10, 8))
        self.students = ttk.Treeview(student_frame, columns=("scholar", "name", "father", "class", "phone"), show="headings", height=5)
        for key, heading, width in (("scholar", "Scholar No.", 100), ("name", "Student", 220),
                                    ("father", "Father's Name", 220), ("class", "Class", 110), ("phone", "Mobile", 120)):
            self.students.heading(key, text=heading); self.students.column(key, width=width, anchor="w")
        student_scroll = ttk.Scrollbar(student_frame, orient="vertical", command=self.students.yview)
        self.students.configure(yscrollcommand=student_scroll.set)
        self.students.pack(side="left", fill="x", expand=True); student_scroll.pack(side="right", fill="y")
        self.students.bind("<<TreeviewSelect>>", self.load)
        self.summary = ttk.Label(page, text="Select a student.", style="Muted.TLabel", wraplength=1120, justify="left")
        self.summary.pack(fill="x", pady=(2, 8))
        columns = ("date", "academic_year", "type", "reference", "description", "debit", "credit", "balance")
        events_frame = ttk.Frame(page)
        events_frame.pack(fill="both", expand=True)
        self.events = ttk.Treeview(events_frame, columns=columns, show="headings")
        for key, heading, width in (("date", "Date", 135), ("academic_year", "Academic Year", 105),
                                    ("type", "Entry", 105), ("reference", "Receipt / Ref", 135),
                                    ("description", "Details", 300), ("debit", "Debit", 95),
                                    ("credit", "Credit", 100), ("balance", "Running Balance", 120)):
            self.events.heading(key, text=heading); self.events.column(key, width=width, anchor="w")
        event_scroll = ttk.Scrollbar(events_frame, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=event_scroll.set)
        self.events.pack(side="left", fill="both", expand=True); event_scroll.pack(side="right", fill="y")

    def search(self) -> None:
        for item in self.students.get_children(): self.students.delete(item)
        term = f"%{self.search_var.get().strip()}%"
        with connect_db() as conn:
            rows = conn.execute("""SELECT id,scholar_no,name,father_name,class,section,phone FROM students
                WHERE name LIKE ? OR scholar_no LIKE ? OR father_name LIKE ? OR phone LIKE ? ORDER BY is_active DESC,class,name LIMIT 300""",
                (term, term, term, term)).fetchall()
        for row in rows:
            class_text = f"{row['class'] or ''}{' / ' + row['section'] if row['section'] else ''}"
            self.students.insert("", "end", iid=str(row["id"]), values=(row["scholar_no"] or "", row["name"],
                row["father_name"] or "", class_text, row["phone"] or ""))

    def load(self, _event=None) -> None:
        selected = self.students.selection()
        if not selected: return
        with connect_db() as conn:
            register = student_dues_register(conn, int(selected[0]))
        student, totals = register["student"], register["totals"]
        class_text = f"{student.get('class') or '-'}{(' / ' + student.get('section')) if student.get('section') else ''}"
        self.summary.configure(text=(
            f"{student.get('name')}  |  Scholar No.: {student.get('scholar_no') or '-'}  |  Class: {class_text}  |  "
            f"Father: {student.get('father_name') or '-'}  |  Mother: {student.get('mother_name') or '-'}\n"
            f"Mobile: {student.get('phone') or '-'} / {student.get('mobile2') or '-'}  |  "
            f"Admission: {student.get('admission_date') or '-'}  |  Address: {student.get('address') or '-'}\n"
            f"Charged till date: {format_currency(totals['charged'])}  |  Paid till date: {format_currency(totals['paid'])}  |  "
            f"Discounts/Exemptions: {format_currency(totals['adjustments'])}  |  "
            f"Outstanding: {format_currency(totals['outstanding'])}"
        ))
        for item in self.events.get_children(): self.events.delete(item)
        balance = 0.0
        for index, event in enumerate(register["events"]):
            balance += event["debit"] - event["credit"]
            self.events.insert("", "end", iid=str(index), values=(event["date"], event.get("academic_year", ""),
                event["type"], event["reference"],
                event["description"], format_currency(event["debit"]) if event["debit"] else "",
                format_currency(event["credit"]) if event["credit"] else "", format_currency(balance)))
