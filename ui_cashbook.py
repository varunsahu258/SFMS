"""Cashbook management workspace for income, expenses, vouchers, and bank analysis."""

from __future__ import annotations

import tkinter as tk
import os
import subprocess
from tkinter import filedialog, messagebox, simpledialog, ttk

import auth
from cashbook_service import (
    ACCOUNT_CASH,
    PAYMENT_METHODS,
    add_account,
    add_transaction,
    cashbook_audit_rows,
    collection_candidates,
    import_bank_statement_csv,
    import_collection_receipts,
    install_cashbook_schema,
    latest_bank_rows,
    list_accounts,
    list_heads,
    parse_date,
    print_cashbook_audit_report,
    print_cashbook_report,
    print_voucher,
    set_head_active,
    summary,
    transactions,
    upsert_head,
    vehicle_expenses_by_head,
)
from ui_date import DateEntry
from ui_master_utils import connect_db, ensure_permission_write
from ui_workspace import WorkspacePage
from utils import format_currency, today_str


class CashbookWindow(WorkspacePage):
    """Manage school cashbook transactions and bank-statement analysis."""

    @auth.require_permission("view_cashbook")
    def __init__(self, master=None, *, embedded: bool = False, initial_tab: str = "transactions"):
        super().__init__(master, embedded=embedded)
        self.title("Cashbook")
        self.geometry("1280x760")
        self.start_var = tk.StringVar(value=today_str())
        self.end_var = tk.StringVar(value=today_str())
        self.search_var = tk.StringVar()
        self.account_var = tk.StringVar(value=ACCOUNT_CASH)
        self.head_filter_var = tk.StringVar(value="All Heads")
        self.income_vars: dict[int, tk.BooleanVar] = {}
        self.initial_tab = initial_tab
        self.tabs: dict[str, ttk.Frame] = {}
        self.include_main = tk.BooleanVar(value=True)
        self.include_small = tk.BooleanVar(value=True)
        self.include_exemption = tk.BooleanVar(value=True)
        with connect_db() as conn:
            install_cashbook_schema(conn)
        self._build_widgets()
        self.refresh_all()
        self.show_tab(initial_tab)

    def show_tab(self, tab_key: str) -> None:
        tab = self.tabs.get(tab_key)
        if tab is not None:
            self.notebook.select(tab)

    def _build_widgets(self) -> None:
        page = ttk.Frame(self, padding=16)
        page.pack(fill="both", expand=True)
        title = ttk.Frame(page)
        title.pack(fill="x")
        ttk.Label(title, text="Cashbook", style="Title.TLabel").pack(side="left")
        ttk.Button(title, text="Print Cashbook", command=self.print_cashbook, style="Accent.TButton").pack(side="right")

        filters = ttk.LabelFrame(page, text="Custom Period / Search", padding=10)
        filters.pack(fill="x", pady=(10, 8))
        ttk.Label(filters, text="From").pack(side="left")
        DateEntry(filters, textvariable=self.start_var, width=12).pack(side="left", padx=5)
        ttk.Label(filters, text="To").pack(side="left", padx=(8, 0))
        DateEntry(filters, textvariable=self.end_var, width=12).pack(side="left", padx=5)
        ttk.Label(filters, text="Search").pack(side="left", padx=(10, 0))
        entry = ttk.Entry(filters, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=5)
        entry.bind("<KeyRelease>", lambda _event: self.refresh_all())
        ttk.Button(filters, text="Refresh", command=self.refresh_all).pack(side="left", padx=5)
        ttk.Button(filters, text="Add Account", command=self.add_account_dialog).pack(side="left", padx=5)

        self.summary_var = tk.StringVar()
        ttk.Label(page, textvariable=self.summary_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        self.notebook = ttk.Notebook(page)
        self.notebook.pack(fill="both", expand=True)
        self._build_transactions_tab()
        self._build_add_tab("EXPENSE")
        self._build_add_tab("INCOME")
        self._build_balances_tab()
        self._build_import_tab()
        self._build_vouchers_tab()
        self._build_audit_tab()
        self._build_heads_tab()
        self._build_bank_tab()

    def _build_transactions_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.tabs["transactions"] = tab
        self.notebook.add(tab, text="View / Search Transactions")
        columns = ("date", "type", "head", "description", "method", "account", "reference", "amount", "voucher")
        self.transaction_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        headings = (
            ("date", "Date", 90), ("type", "Type", 80), ("head", "Head", 150),
            ("description", "Description", 290), ("method", "Payment Method", 115),
            ("account", "Account", 115), ("reference", "Reference", 120),
            ("amount", "Amount", 100), ("voucher", "Voucher", 95),
        )
        for column, heading, width in headings:
            self.transaction_tree.heading(column, text=heading)
            self.transaction_tree.column(column, width=width, anchor="w")
        self.transaction_tree.pack(fill="both", expand=True)
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Print Selected Voucher", command=self.print_selected_voucher).pack(side="left")

    def _build_add_tab(self, txn_type: str) -> None:
        title = "Income Adder" if txn_type == "INCOME" else "Expense Adder"
        tab = ttk.Frame(self.notebook, padding=16)
        self.tabs["income" if txn_type == "INCOME" else "expense"] = tab
        self.notebook.add(tab, text=title)
        vars_ = {
            "date": tk.StringVar(value=today_str()),
            "head": tk.StringVar(),
            "amount": tk.StringVar(),
            "method": tk.StringVar(value="CASH"),
            "account": tk.StringVar(value=ACCOUNT_CASH),
            "reference": tk.StringVar(),
            "counterparty": tk.StringVar(),
            "description": tk.StringVar(),
        }
        setattr(self, f"{txn_type.lower()}_form", vars_)
        for row, (label, key) in enumerate((
            ("Date", "date"), ("Head", "head"), ("Amount", "amount"),
            ("Payment Method", "method"), ("Account", "account"), ("Reference", "reference"),
            ("Received From" if txn_type == "INCOME" else "Paid To", "counterparty"),
            ("Description", "description"),
        )):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
            if key == "date":
                widget = DateEntry(tab, textvariable=vars_[key], width=16)
            elif key == "head":
                widget = ttk.Combobox(tab, textvariable=vars_[key], state="readonly", width=34)
                setattr(self, f"{txn_type.lower()}_head_combo", widget)
            elif key == "method":
                widget = ttk.Combobox(tab, textvariable=vars_[key], values=PAYMENT_METHODS, state="readonly", width=22)
            elif key == "account":
                widget = ttk.Combobox(tab, textvariable=vars_[key], state="readonly", width=24)
                setattr(self, f"{txn_type.lower()}_account_combo", widget)
            else:
                widget = ttk.Entry(tab, textvariable=vars_[key], width=38)
            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        tab.columnconfigure(1, weight=1)
        ttk.Button(tab, text=f"Save {txn_type.title()}", command=lambda t=txn_type: self.save_manual_transaction(t), style="Accent.TButton").grid(row=9, column=0, columnspan=2, pady=16)

    def _build_heads_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["heads"] = tab
        self.notebook.add(tab, text="Head Manager")
        form = ttk.LabelFrame(tab, text="Add Head", padding=10)
        form.pack(fill="x")
        self.head_name_var = tk.StringVar()
        self.head_category_var = tk.StringVar(value="EXPENSE")
        ttk.Label(form, text="Name").pack(side="left")
        ttk.Entry(form, textvariable=self.head_name_var, width=32).pack(side="left", padx=6)
        ttk.Label(form, text="Category").pack(side="left", padx=(8, 0))
        ttk.Combobox(form, textvariable=self.head_category_var, values=("INCOME", "EXPENSE", "VEHICLE"), state="readonly", width=12).pack(side="left", padx=6)
        ttk.Button(form, text="Save Head", command=self.save_head, style="Accent.TButton").pack(side="left", padx=8)
        columns = ("name", "category", "active")
        self.head_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in (("name", "Head", 260), ("category", "Category", 120), ("active", "Active", 80)):
            self.head_tree.heading(column, text=heading)
            self.head_tree.column(column, width=width)
        self.head_tree.pack(fill="both", expand=True, pady=10)
        ttk.Button(tab, text="Toggle Active", command=self.toggle_head).pack(anchor="w")

    def _build_import_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["import"] = tab
        self.notebook.add(tab, text="Import Collections")
        opts = ttk.LabelFrame(tab, text="Collections to include as income", padding=10)
        opts.pack(fill="x")
        ttk.Checkbutton(opts, text="Main Collection", variable=self.include_main, command=self.load_collection_candidates).pack(side="left")
        ttk.Checkbutton(opts, text="Small Collection", variable=self.include_small, command=self.load_collection_candidates).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Exemption Collection", variable=self.include_exemption, command=self.load_collection_candidates).pack(side="left", padx=8)
        ttk.Label(opts, text="Deposit Account").pack(side="left", padx=(16, 0))
        self.import_account_var = tk.StringVar(value=ACCOUNT_CASH)
        self.import_account_combo = ttk.Combobox(opts, textvariable=self.import_account_var, state="readonly", width=18)
        self.import_account_combo.pack(side="left", padx=6)
        ttk.Button(opts, text="Load Receipts", command=self.load_collection_candidates).pack(side="left", padx=6)
        ttk.Button(opts, text="Import Selected", command=self.import_selected_collections, style="Accent.TButton").pack(side="left")
        self.collection_tree = ttk.Treeview(tab, columns=("include", "receipt", "type", "student", "amount", "method"), show="headings")
        for column, heading, width in (("include", "Include", 70), ("receipt", "Receipt", 130), ("type", "Collection", 150), ("student", "Student", 230), ("amount", "Amount", 100), ("method", "Method", 90)):
            self.collection_tree.heading(column, text=heading)
            self.collection_tree.column(column, width=width)
        self.collection_tree.pack(fill="both", expand=True, pady=10)
        self.collection_tree.bind("<Button-1>", self._toggle_collection_row)

    def _build_bank_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["bank"] = tab
        self.notebook.add(tab, text="Bank Statements")
        controls = ttk.Frame(tab)
        controls.pack(fill="x")
        self.bank_name_var = tk.StringVar(value="Central Bank of India")
        ttk.Label(controls, text="Bank").pack(side="left")
        ttk.Entry(controls, textvariable=self.bank_name_var, width=26).pack(side="left", padx=6)
        ttk.Button(controls, text="Upload CSV and Analyse", command=self.upload_bank_statement, style="Accent.TButton").pack(side="left")
        ttk.Label(controls, text="CSV headers can include Date/Narration/Debit/Credit/Balance/Reference.", style="Muted.TLabel").pack(side="left", padx=12)
        columns = ("bank", "date", "description", "debit", "credit", "balance", "reference", "analysis")
        self.bank_tree = ttk.Treeview(tab, columns=columns, show="headings")
        for column, heading, width in (("bank", "Bank", 120), ("date", "Date", 90), ("description", "Description", 320), ("debit", "Debit", 95), ("credit", "Credit", 95), ("balance", "Balance", 95), ("reference", "Reference", 120), ("analysis", "Analysis", 110)):
            self.bank_tree.heading(column, text=heading)
            self.bank_tree.column(column, width=width)
        self.bank_tree.pack(fill="both", expand=True, pady=10)

    def _build_balances_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["balances"] = tab
        self.notebook.add(tab, text="Balances / Vehicle Expenses")
        self.balance_text = tk.Text(tab, height=12, wrap="word", state="disabled")
        self.balance_text.pack(fill="x")
        ttk.Label(tab, text="Vehicle Expenses according to head", style="Title.TLabel").pack(anchor="w", pady=(12, 4))
        self.vehicle_tree = ttk.Treeview(tab, columns=("head", "count", "total"), show="headings", height=8)
        for column, heading, width in (("head", "Vehicle Head", 240), ("count", "Entries", 100), ("total", "Total", 130)):
            self.vehicle_tree.heading(column, text=heading)
            self.vehicle_tree.column(column, width=width)
        self.vehicle_tree.pack(fill="both", expand=True)

    def _build_vouchers_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["vouchers"] = tab
        self.notebook.add(tab, text="Vouchers / Bills")
        ttk.Label(tab, text="Select a transaction to generate or re-open its receipt/payment voucher.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        columns = ("date", "type", "head", "counterparty", "amount", "voucher")
        self.voucher_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in (
            ("date", "Date", 90), ("type", "Type", 90), ("head", "Head", 180),
            ("counterparty", "Party", 240), ("amount", "Amount", 110), ("voucher", "Voucher", 120),
        ):
            self.voucher_tree.heading(column, text=heading)
            self.voucher_tree.column(column, width=width)
        self.voucher_tree.pack(fill="both", expand=True)
        ttk.Button(tab, text="Print Selected Voucher / Bill", command=self.print_selected_voucher, style="Accent.TButton").pack(anchor="w", pady=8)

    def _build_audit_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.tabs["audit"] = tab
        self.notebook.add(tab, text="Audit Reports")
        ttk.Button(tab, text="Print Cashbook Audit Report", command=self.print_audit_report, style="Accent.TButton").pack(anchor="w", pady=(0, 8))
        columns = ("id", "date", "type", "head", "amount", "source", "receipt", "voucher", "created", "user")
        self.audit_tree = ttk.Treeview(tab, columns=columns, show="headings")
        for column, heading, width in (
            ("id", "ID", 60), ("date", "Date", 90), ("type", "Type", 80), ("head", "Head", 150),
            ("amount", "Amount", 100), ("source", "Source", 130), ("receipt", "Receipt/Ref", 130),
            ("voucher", "Voucher", 115), ("created", "Created", 150), ("user", "User", 120),
        ):
            self.audit_tree.heading(column, text=heading)
            self.audit_tree.column(column, width=width)
        self.audit_tree.pack(fill="both", expand=True)

    def refresh_all(self) -> None:
        auth.touch_session()
        self.refresh_lookups()
        self.refresh_transactions()
        self.refresh_heads()
        self.refresh_balances()
        self.refresh_bank_rows()
        self.refresh_vouchers()
        self.refresh_audit_rows()

    def refresh_lookups(self) -> None:
        with connect_db() as conn:
            accounts = list_accounts(conn)
            income_heads = [f"{row['id']} - {row['name']}" for row in list_heads(conn, "INCOME")]
            expense_heads = [f"{row['id']} - {row['name']}" for row in list_heads(conn, "EXPENSE")]
        for combo_name in ("income_account_combo", "expense_account_combo", "import_account_combo"):
            combo = getattr(self, combo_name, None)
            if combo is not None:
                combo.configure(values=accounts)
        if accounts and self.import_account_var.get() not in accounts:
            self.import_account_var.set(accounts[0])
        if hasattr(self, "income_head_combo"):
            self.income_head_combo.configure(values=income_heads)
            if income_heads and not self.income_form["head"].get():
                self.income_form["head"].set(income_heads[0])
        if hasattr(self, "expense_head_combo"):
            self.expense_head_combo.configure(values=expense_heads)
            if expense_heads and not self.expense_form["head"].get():
                self.expense_form["head"].set(expense_heads[0])
        for form_name in ("income_form", "expense_form"):
            form = getattr(self, form_name, None)
            if form and accounts and form["account"].get() not in accounts:
                form["account"].set(accounts[0])

    def refresh_transactions(self) -> None:
        for item in self.transaction_tree.get_children():
            self.transaction_tree.delete(item)
        with connect_db() as conn:
            rows = transactions(conn, self.start_var.get(), self.end_var.get(), self.search_var.get())
        for row in rows:
            self.transaction_tree.insert("", "end", iid=str(row["id"]), values=(
                row["txn_date"], row["txn_type"], row["head_name"], row["description"] or "",
                row["payment_method"], row["account_name"], row["reference"] or "",
                format_currency(row["amount"]), row["voucher_no"] or "",
            ))

    def refresh_vouchers(self) -> None:
        if not hasattr(self, "voucher_tree"):
            return
        self.voucher_tree.delete(*self.voucher_tree.get_children())
        with connect_db() as conn:
            rows = transactions(conn, self.start_var.get(), self.end_var.get(), self.search_var.get())
        for row in rows:
            self.voucher_tree.insert("", "end", iid=str(row["id"]), values=(
                row["txn_date"], row["txn_type"], row["head_name"], row["counterparty"] or "",
                format_currency(row["amount"]), row["voucher_no"] or "",
            ))

    def refresh_audit_rows(self) -> None:
        if not hasattr(self, "audit_tree"):
            return
        self.audit_tree.delete(*self.audit_tree.get_children())
        with connect_db() as conn:
            rows = cashbook_audit_rows(conn, self.start_var.get(), self.end_var.get())
        for row in rows:
            self.audit_tree.insert("", "end", iid=str(row["id"]), values=(
                row["id"], row["txn_date"], row["txn_type"], row["head_name"],
                format_currency(row["amount"]), row["source_type"],
                row["receipt_no"] or row["reference"] or "", row["voucher_no"] or "",
                row["created_at"], row["created_by_name"] or "",
            ))

    def refresh_heads(self) -> None:
        for item in self.head_tree.get_children():
            self.head_tree.delete(item)
        with connect_db() as conn:
            rows = list_heads(conn, active_only=False)
        for row in rows:
            self.head_tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["category"], "Yes" if row["is_active"] else "No"))

    def refresh_balances(self) -> None:
        with connect_db() as conn:
            state = summary(conn, self.start_var.get(), self.end_var.get())
            vehicle_rows = vehicle_expenses_by_head(conn, self.start_var.get(), self.end_var.get())
        text = [
            f"Previous Balance: {format_currency(state['previous_balance'])}",
            f"Income Total: {format_currency(state['income_total'])}",
            f"Expense Total: {format_currency(state['expense_total'])}",
            f"Balance: {format_currency(state['balance'])}",
            "",
            "Account Balances / Cash Balances:",
        ]
        for account, balance in state["account_balances"].items():
            text.append(f"  {account}: {format_currency(balance)}")
        self.summary_var.set("  |  ".join(text[:4]))
        self.balance_text.configure(state="normal")
        self.balance_text.delete("1.0", "end")
        self.balance_text.insert("1.0", "\n".join(text))
        self.balance_text.configure(state="disabled")
        for item in self.vehicle_tree.get_children():
            self.vehicle_tree.delete(item)
        for row in vehicle_rows:
            self.vehicle_tree.insert("", "end", values=(row["name"], row["count"], format_currency(row["total"])))

    def refresh_bank_rows(self) -> None:
        for item in self.bank_tree.get_children():
            self.bank_tree.delete(item)
        with connect_db() as conn:
            rows = latest_bank_rows(conn)
        for row in rows:
            self.bank_tree.insert("", "end", values=(
                row["bank_name"], row["txn_date"] or "", row["description"] or "",
                format_currency(row["debit"] or 0), format_currency(row["credit"] or 0),
                format_currency(row["balance"] or 0) if row["balance"] is not None else "",
                row["reference"] or "", row["analysis_note"] or "",
            ))

    def _selected_head_id(self, form: dict[str, tk.StringVar]) -> int:
        value = form["head"].get()
        try:
            return int(value.split(" - ", 1)[0])
        except (ValueError, IndexError) as exc:
            raise ValueError("Select a head.") from exc

    @auth.require_permission("manage_cashbook")
    def save_manual_transaction(self, txn_type: str) -> None:
        if not ensure_permission_write("manage_cashbook"):
            return
        form = getattr(self, f"{txn_type.lower()}_form")
        try:
            head_id = self._selected_head_id(form)
            with connect_db() as conn:
                add_transaction(
                    conn,
                    txn_date=form["date"].get(), txn_type=txn_type, head_id=head_id,
                    description=form["description"].get(), amount=form["amount"].get(),
                    payment_method=form["method"].get(), account_name=form["account"].get(),
                    reference=form["reference"].get(), counterparty=form["counterparty"].get(),
                    user_id=auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None,
                )
        except Exception as exc:
            messagebox.showerror("Cashbook", str(exc), parent=self)
            return
        for key in ("amount", "reference", "counterparty", "description"):
            form[key].set("")
        messagebox.showinfo("Cashbook", f"{txn_type.title()} saved.", parent=self)
        self.refresh_all()

    @auth.require_permission("manage_cashbook")
    def save_head(self) -> None:
        if not ensure_permission_write("manage_cashbook"):
            return
        try:
            with connect_db() as conn:
                upsert_head(conn, self.head_name_var.get(), self.head_category_var.get(), auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None)
        except Exception as exc:
            messagebox.showerror("Head Manager", str(exc), parent=self)
            return
        self.head_name_var.set("")
        self.refresh_all()

    @auth.require_permission("manage_cashbook")
    def toggle_head(self) -> None:
        selected = self.head_tree.selection()
        if not selected:
            return
        with connect_db() as conn:
            row = conn.execute("SELECT is_active FROM cashbook_heads WHERE id=?", (int(selected[0]),)).fetchone()
            if row:
                set_head_active(conn, int(selected[0]), not bool(row[0]))
        self.refresh_all()

    @auth.require_permission("manage_cashbook")
    def add_account_dialog(self) -> None:
        name = simpledialog.askstring("Cashbook Account", "Account name (for example CBI, Gramin Bank, FDR):", parent=self)
        if not name:
            return
        opening = simpledialog.askstring("Opening Balance", "Opening balance:", initialvalue="0", parent=self)
        try:
            with connect_db() as conn:
                add_account(conn, name, opening or 0, auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None)
        except Exception as exc:
            messagebox.showerror("Cashbook Account", str(exc), parent=self)
            return
        self.refresh_all()

    def load_collection_candidates(self) -> None:
        for item in self.collection_tree.get_children():
            self.collection_tree.delete(item)
        self.income_vars.clear()
        with connect_db() as conn:
            rows = collection_candidates(conn, self.include_main.get(), self.include_small.get(), self.include_exemption.get())
        for row in rows:
            self.income_vars[int(row["receipt_id"])] = tk.BooleanVar(value=True)
            self.collection_tree.insert("", "end", iid=str(row["receipt_id"]), values=(
                "Yes", row["receipt_no"], row["receipt_type"] or "", row["student_name"] or "",
                format_currency(row["total_paid"] or 0), row["payment_mode"] or "CASH",
            ))

    def _toggle_collection_row(self, event) -> None:
        row_id = self.collection_tree.identify_row(event.y)
        column = self.collection_tree.identify_column(event.x)
        if not row_id or column != "#1":
            return
        var = self.income_vars.get(int(row_id))
        if var is None:
            return
        var.set(not var.get())
        values = list(self.collection_tree.item(row_id, "values"))
        values[0] = "Yes" if var.get() else "No"
        self.collection_tree.item(row_id, values=values)

    @auth.require_permission("manage_cashbook")
    def import_selected_collections(self) -> None:
        if not ensure_permission_write("manage_cashbook"):
            return
        selected = [receipt_id for receipt_id, var in self.income_vars.items() if var.get()]
        try:
            with connect_db() as conn:
                count = import_collection_receipts(conn, selected, self.import_account_var.get(), auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None)
        except Exception as exc:
            messagebox.showerror("Import Collections", str(exc), parent=self)
            return
        messagebox.showinfo("Import Collections", f"Imported {count} receipt(s) as income.", parent=self)
        self.load_collection_candidates()
        self.refresh_all()

    @auth.require_permission("manage_cashbook")
    def upload_bank_statement(self) -> None:
        if not ensure_permission_write("manage_cashbook"):
            return
        path = filedialog.askopenfilename(parent=self, filetypes=(("CSV files", "*.csv"), ("All files", "*.*")))
        if not path:
            return
        try:
            with connect_db() as conn:
                count, matched = import_bank_statement_csv(conn, path, self.bank_name_var.get(), auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION else None)
        except Exception as exc:
            messagebox.showerror("Bank Statement", str(exc), parent=self)
            return
        messagebox.showinfo("Bank Statement", f"Imported {count} statement row(s); matched {matched} with cashbook transactions.", parent=self)
        self.refresh_bank_rows()

    def print_cashbook(self) -> None:
        try:
            parse_date(self.start_var.get()); parse_date(self.end_var.get())
            with connect_db() as conn:
                path = print_cashbook_report(conn, self.start_var.get(), self.end_var.get())
        except Exception as exc:
            messagebox.showerror("Print Cashbook", str(exc), parent=self)
            return
        self._open_pdf(path)
        messagebox.showinfo("Print Cashbook", f"Cashbook saved and opened:\n{path}", parent=self)

    def print_selected_voucher(self) -> None:
        active_tree = self.voucher_tree if hasattr(self, "voucher_tree") and self.notebook.select() == str(self.voucher_tree.master) else self.transaction_tree
        selected = active_tree.selection()
        if not selected:
            messagebox.showwarning("Voucher", "Select a transaction first.", parent=self)
            return
        try:
            with connect_db() as conn:
                path = print_voucher(conn, int(selected[0]))
        except Exception as exc:
            messagebox.showerror("Voucher", str(exc), parent=self)
            return
        self._open_pdf(path)
        messagebox.showinfo("Voucher", f"Voucher saved and opened:\n{path}", parent=self)

    def print_audit_report(self) -> None:
        try:
            with connect_db() as conn:
                path = print_cashbook_audit_report(conn, self.start_var.get(), self.end_var.get())
        except Exception as exc:
            messagebox.showerror("Cashbook Audit", str(exc), parent=self)
            return
        self._open_pdf(path)
        messagebox.showinfo("Cashbook Audit", f"Audit report saved and opened:\n{path}", parent=self)

    def _open_pdf(self, path: str) -> None:
        if hasattr(os, "startfile"):
            os.startfile(path)
        elif os.name == "posix":
            opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
