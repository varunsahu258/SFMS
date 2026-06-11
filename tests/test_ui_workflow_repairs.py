"""Regression checks for repaired collection, dues, discount, and report workflows."""

from __future__ import annotations

import inspect


def test_discount_applies_exactly_one_ledger_adjustment_and_labels_search():
    from ui_discount import DiscountWindow

    save_source = inspect.getsource(DiscountWindow.save)
    build_source = inspect.getsource(DiscountWindow._build_widgets)
    assert save_source.count("add_adjustment(") == 1
    assert 'text="Search Student"' in build_source


def test_advance_payment_has_mode_details_and_confirmation():
    from ui_advance_payment import AdvancePaymentWindow

    build_source = inspect.getsource(AdvancePaymentWindow._build_widgets)
    save_source = inspect.getsource(AdvancePaymentWindow.save)
    detail_source = inspect.getsource(AdvancePaymentWindow.capture_mode_detail)
    assert "MODE_LABELS" in build_source
    assert "ChequeDetailDialog" in detail_source
    assert "UPIDetailDialog" in detail_source
    assert "askyesno" in save_source
    assert "selected_student_name" in save_source


def test_main_collection_keeps_fixed_disabled_action_and_scrollable_fee_list():
    from ui_collection_main import CollectionMainWindow

    build_source = inspect.getsource(CollectionMainWindow._build_widgets)
    load_source = inspect.getsource(CollectionMainWindow.load_dues)
    assert 'state="disabled"' in build_source
    assert "self.save_button" in build_source
    assert "tk.Canvas" in load_source
    assert "ttk.Scrollbar" in load_source
    assert 'state="normal" if self.fee_items else "disabled"' in load_source


def test_dues_export_requires_class_state_and_confirmation():
    from ui_dues import DuesWindow

    build_source = inspect.getsource(DuesWindow._build_widgets)
    export_source = inspect.getsource(DuesWindow.export)
    assert 'state="disabled"' in build_source
    assert "<<ComboboxSelected>>" in build_source
    assert "askyesno" in export_source
    assert "father_name" in inspect.getsource(DuesWindow.load_dues)


def test_small_collection_warns_when_rows_are_truncated():
    from ui_collection_common import CollectionBaseWindow

    source = inspect.getsource(CollectionBaseWindow.load_dues)
    assert "hidden_count" in source
    assert "additional fee head(s) are not shown" in source
    assert "Use Main Collection" in source


def test_collection_report_date_entries_start_disabled():
    from ui_reports import ReportsWindow

    source = inspect.getsource(ReportsWindow._build_collection_tab)
    assert source.count('state="disabled"') >= 2
