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


def test_dashboard_uses_management_cards_instead_of_left_sidebar():
    import ui_dashboard

    build_source = inspect.getsource(ui_dashboard.DashboardWindow._build_widgets)
    dashboard_source = inspect.getsource(ui_dashboard.DashboardWindow._show_dashboard)
    groups_source = inspect.getsource(ui_dashboard.DashboardWindow._module_groups)
    assert "self.workspace" in build_source
    assert "sidebar.pack" not in build_source
    assert "_management_card" in dashboard_source
    for title in (
        "Fees Management", "Cashbook Management", "Timetable Management",
        "Student Management", "Exam Management", "Result Management",
    ):
        assert title in groups_source


def test_management_groups_route_existing_modules_and_mark_future_modules_planned():
    import ui_dashboard

    source = inspect.getsource(ui_dashboard.DashboardWindow._module_groups)
    for handler in (
        "_on_main_collection_click", "_on_dues_register_click", "_on_reports_click",
        "_on_admissions_click", "_on_students_click", "_on_classes_click",
        "_on_timetable_click",
    ):
        assert handler in source
    for planned in (
        "Daily Cashbook", "Exam Timetable", "Paper Management",
        "Marksheet Generation", "Result Diary for PTMs",
        "Conveyance Details Management",
    ):
        assert planned in source


def test_management_page_does_not_repeat_top_header_title_or_description():
    import ui_dashboard

    source = inspect.getsource(ui_dashboard.DashboardWindow._show_module_group)
    assert 'self.workspace_title.set(group["title"])' in source
    assert 'text=group["title"]' not in source
    assert 'text=group["description"]' not in source
    assert 'text="← Back to Dashboard"' in source


def test_embedded_modules_use_section_header_and_shared_scroll_canvas():
    import ui_dashboard

    source = inspect.getsource(ui_dashboard.DashboardWindow._show_workspace_page)
    assert "_workspace_section_title(key, title)" in source
    assert "_create_workspace_canvas()" in source
    assert "canvas.create_window" in source
    assert 'embedded=True' in source


def test_workspace_mousewheel_supports_all_desktop_platform_events():
    from types import SimpleNamespace

    import ui_dashboard

    normalize = ui_dashboard.DashboardWindow._mousewheel_units
    assert normalize(SimpleNamespace(delta=120, num=None)) < 0
    assert normalize(SimpleNamespace(delta=-120, num=None)) > 0
    assert normalize(SimpleNamespace(delta=1, num=None)) < 0
    assert normalize(SimpleNamespace(delta=-1, num=None)) > 0
    assert normalize(SimpleNamespace(delta=0, num=4)) < 0
    assert normalize(SimpleNamespace(delta=0, num=5)) > 0

    bindings = inspect.getsource(ui_dashboard.DashboardWindow._bind_shortcuts)
    assert '"<MouseWheel>"' in bindings
    assert '"<Button-4>"' in bindings
    assert '"<Button-5>"' in bindings
