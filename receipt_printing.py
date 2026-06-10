"""Post-commit receipt printing, failure recording, and retry helpers."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from config import DB_PATH
from utils import now_str

Printer = Callable[[sqlite3.Connection, str, bool], str]


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def record_print_failure(conn: sqlite3.Connection, receipt_id: int, error: Exception | str) -> None:
    """Persist a post-commit print failure independently of financial data."""
    conn.execute(
        "INSERT INTO receipt_print_failures(receipt_id,failed_at,error_message) VALUES(?,?,?)",
        (receipt_id, now_str(), str(error)[:2000]),
    )


def print_committed_receipt(
    receipt_id: int,
    receipt_no: str,
    *,
    reprint: bool = False,
    printer: Printer | None = None,
    db_path: str = DB_PATH,
) -> str:
    """Print exclusively from a fresh connection containing committed records."""
    if printer is None:
        from receipt_printer import print_receipt

        printer = print_receipt
    try:
        with _connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM receipts WHERE id=? AND receipt_no=?", (receipt_id, receipt_no)
            ).fetchone()
            if not exists:
                raise ValueError("Committed receipt was not found.")
            return printer(conn, receipt_no, reprint)
    except Exception as exc:
        with _connect(db_path) as failure_conn:
            record_print_failure(failure_conn, receipt_id, exc)
        raise


def commit_then_print(
    conn: sqlite3.Connection,
    receipt_id: int,
    receipt_no: str,
    *,
    printer: Printer | None = None,
    db_path: str = DB_PATH,
) -> str:
    """Commit financial work first, then print through a new database connection."""
    conn.commit()
    return print_committed_receipt(
        receipt_id, receipt_no, printer=printer, db_path=db_path
    )


def retry_committed_receipt(receipt_id: int, receipt_no: str, *, db_path: str = DB_PATH) -> str:
    """Retry from committed data, preserving ORIGINAL when no original PDF exists yet."""
    with _connect(db_path) as conn:
        original_exists = conn.execute(
            "SELECT 1 FROM receipt_print_history WHERE receipt_id=? AND print_type='ORIGINAL'",
            (receipt_id,),
        ).fetchone() is not None
    return print_committed_receipt(
        receipt_id, receipt_no, reprint=original_exists, db_path=db_path
    )


class PrintFailureDialog(tk.Toplevel):
    """Non-blocking print error with an explicit committed-record reprint option."""

    def __init__(self, master, receipt_id: int, receipt_no: str, error: Exception):
        super().__init__(master)
        self.receipt_id = receipt_id
        self.receipt_no = receipt_no
        self.title("Receipt Saved — Printing Failed")
        self.transient(master)
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(f"Payment {receipt_no} was committed successfully, but the PDF could not be generated.\n"
                  f"{error}"),
            wraplength=500,
        ).pack(anchor="w", pady=(0, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Reprint", command=self._retry).pack(side="left")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

    def _retry(self) -> None:
        try:
            path = retry_committed_receipt(self.receipt_id, self.receipt_no)
        except Exception as exc:
            messagebox.showerror("Reprint failed", str(exc), parent=self)
            return
        messagebox.showinfo("Receipt", f"Receipt saved to:\n{path}", parent=self)
        self.destroy()
