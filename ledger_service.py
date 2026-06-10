"""Single authoritative service for derived SFMS outstanding balances."""

from __future__ import annotations

import sqlite3
from decimal import Decimal


class LedgerService:
    """Calculate dues only from immutable charges, allocations, and adjustments."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _decimal(value) -> Decimal:
        return Decimal(str(value or 0))

    def _year_label(self, academic_year_id) -> str | None:
        if academic_year_id is None:
            return None
        if isinstance(academic_year_id, str) and not academic_year_id.isdigit():
            return academic_year_id
        else:
            row = self.conn.execute(
                "SELECT label FROM academic_years WHERE id=?", (academic_year_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def get_outstanding(self, student_id, fee_head_id=None, academic_year_id=None) -> Decimal:
        """Return a student's derived outstanding amount for the requested scope."""
        year = self._year_label(academic_year_id)
        conditions = ["c.student_id=?", "c.status<>'CANCELLED'"]
        params: list = [student_id]
        if fee_head_id is not None:
            conditions.append("c.fee_head_id=?")
            params.append(fee_head_id)
        if year is not None:
            conditions.append("c.academic_year=?")
            params.append(year)
        row = self.conn.execute(self._aggregate_sql(" AND ".join(conditions)), params).fetchone()
        return self._decimal(row[0] if row else 0)

    def get_all_outstanding(self, academic_year_id=None) -> list[dict]:
        """Return authoritative positive dues per student, charge, and fee head."""
        year = self._year_label(academic_year_id)
        where = "c.status<>'CANCELLED'"
        params: list = []
        if year is not None:
            where += " AND c.academic_year=?"
            params.append(year)
        cursor = self.conn.execute(
            f"""
            WITH balances AS ({self._balance_rows_sql(where)})
            SELECT b.*,s.name AS student,s.class AS student_class,s.aadhaar,
                   fh.name AS fee_head
            FROM balances b
            JOIN students s ON s.id=b.student_id
            JOIN fee_heads fh ON fh.id=b.fee_head_id
            WHERE b.outstanding>0
            ORDER BY s.class,s.name,fh.name,b.due_date,b.charge_id
            """,
            params,
        )
        columns = [column[0] for column in cursor.description]
        return [dict(row) if hasattr(row, "keys") else dict(zip(columns, row)) for row in cursor.fetchall()]

    @classmethod
    def _aggregate_sql(cls, where: str) -> str:
        return f"SELECT COALESCE(SUM(CASE WHEN outstanding>0 THEN outstanding ELSE 0 END),0) FROM ({cls._balance_rows_sql(where)})"

    @staticmethod
    def _balance_rows_sql(where: str) -> str:
        return f"""
        SELECT c.id AS charge_id,c.student_id,c.academic_year,c.fee_head_id,
               c.original_amount,c.due_date,c.status,
               COALESCE(SUM(CASE WHEN a.allocation_type<>'REVERSAL'
                    AND (UPPER(COALESCE(p.payment_mode,''))<>'CHEQUE' OR p.cheque_status='CLEARED')
                    AND (a.allocation_type<>'ADVANCE' OR
                         (ay.label=c.academic_year AND p.allocated_term=c.due_date))
                    THEN a.amount_allocated ELSE 0 END),0) AS paid,
               COALESCE(adj.adjustments,0) AS adjustments,
               COALESCE(SUM(CASE WHEN a.allocation_type='REVERSAL'
                    THEN a.amount_allocated ELSE 0 END),0) AS reversed,
               c.original_amount
                 - COALESCE(SUM(CASE WHEN a.allocation_type<>'REVERSAL'
                    AND (UPPER(COALESCE(p.payment_mode,''))<>'CHEQUE' OR p.cheque_status='CLEARED')
                    AND (a.allocation_type<>'ADVANCE' OR
                         (ay.label=c.academic_year AND p.allocated_term=c.due_date))
                    THEN a.amount_allocated ELSE 0 END),0)
                 - COALESCE(adj.adjustments,0)
                 + COALESCE(SUM(CASE WHEN a.allocation_type='REVERSAL'
                    THEN a.amount_allocated ELSE 0 END),0) AS outstanding
        FROM student_charges c
        LEFT JOIN payment_allocations a ON a.charge_id=c.id
        LEFT JOIN payments p ON p.id=a.payment_id
        LEFT JOIN academic_years ay ON ay.id=p.allocated_academic_year_id
        LEFT JOIN (
            SELECT charge_id,SUM(amount) AS adjustments
            FROM charge_adjustments
            WHERE adjustment_type IN ('DISCOUNT','EXEMPTION')
            GROUP BY charge_id
        ) adj ON adj.charge_id=c.id
        WHERE {where}
        GROUP BY c.id,c.student_id,c.academic_year,c.fee_head_id,c.original_amount,
                 c.due_date,c.status,adj.adjustments
        """
