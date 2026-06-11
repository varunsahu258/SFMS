"""Regression checks for the single-window dashboard navigation architecture."""

from __future__ import annotations

import inspect

from ui_workspace import WorkspacePage


PRIMARY_PAGES = (
    ("ui_collection_main", "CollectionMainWindow"),
    ("ui_collection_small", "CollectionSmallWindow"),
    ("ui_advance_payment", "AdvancePaymentWindow"),
    ("ui_dues", "DuesWindow"),
    ("ui_students", "StudentWindow"),
    ("ui_classes", "ClassSectionWindow"),
    ("ui_reports", "ReportsWindow"),
    ("ui_backup", "BackupWindow"),
    ("ui_settings", "SettingsWindow"),
    ("ui_timetable", "TimetableWindow"),
)


def test_primary_navigation_destinations_are_embeddable_pages():
    for module_name, class_name in PRIMARY_PAGES:
        module = __import__(module_name, fromlist=[class_name])
        page_class = getattr(module, class_name)
        assert issubclass(page_class, WorkspacePage)
        assert "embedded" in inspect.signature(page_class.__init__).parameters


def test_dashboard_routes_primary_modules_through_workspace_host():
    import ui_dashboard

    source = inspect.getsource(ui_dashboard.DashboardWindow)
    assert "def _show_workspace_page" in source
    assert "embedded=True" in source
    assert 'self._build_daily_tab' not in source  # report internals stay inside their page
    assert 'self._show_workspace_page(StudentWindow, "Students", "students")' in source
    assert 'self._show_workspace_page(ReportsWindow, "Reports", "reports")' in source
    assert 'self._show_workspace_page(BackupWindow, "Backup & Restore", "backup")' in source
    assert 'self._show_workspace_page(TimetableWindow, "Timetable", "timetable")' in source


def test_dashboard_keeps_persistent_navigation_and_overview():
    import ui_dashboard

    source = inspect.getsource(ui_dashboard.DashboardWindow._build_widgets)
    assert "sidebar" in source
    assert "self.workspace" in source
    assert "Dashboard" in source
    assert "Fee Collection" in source
    assert "School Records" in source
    assert "Administration" in source
