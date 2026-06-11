"""Permission catalog for configurable accountant access in SFMS."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDefinition:
    """Describe one delegable application capability."""

    key: str
    label: str
    category: str
    default_for_accountant: bool = False


PERMISSIONS: tuple[PermissionDefinition, ...] = (
    PermissionDefinition("collect_main_fees", "Collect main (BIG/BOTH) fees", "Fee Collection", True),
    PermissionDefinition("collect_small_fees", "Collect small-register fees", "Fee Collection", True),
    PermissionDefinition("collect_exemption_fees", "Collect exemption-aware fees", "Fee Collection", True),
    PermissionDefinition("collect_advance_payments", "Collect advance payments", "Fee Collection", True),
    PermissionDefinition("view_dues", "View, export, and print dues", "Students and Documents", True),
    PermissionDefinition("view_receipts", "Search and view past receipts", "Students and Documents", True),
    PermissionDefinition("view_student_details", "Search and view student details (read-only)", "Students and Documents", True),
    PermissionDefinition("manage_students", "Manage students, imports, promotion, ID cards, and TC", "Students and Documents", True),
    PermissionDefinition("manage_classes", "Manage classes and sections", "Students and Documents", True),
    PermissionDefinition("view_reports", "Open reports and generate report files", "Reports and Notices", True),
    PermissionDefinition("issue_fee_notices", "Generate fee notices", "Reports and Notices"),
    PermissionDefinition("manage_discounts", "Create discounts", "Financial Controls"),
    PermissionDefinition("manage_exemptions", "Create exemptions", "Financial Controls"),
    PermissionDefinition("manage_fee_heads", "Manage fee heads", "Financial Controls"),
    PermissionDefinition("manage_fee_structure", "Manage fee structures", "Financial Controls"),
    PermissionDefinition("apply_late_fees", "Apply late fees to selected students", "Financial Controls"),
    PermissionDefinition("manage_academic_years", "Manage and switch academic years", "Financial Controls"),
    PermissionDefinition("reprint_receipts", "Search and reprint receipts", "Sensitive Financial Actions"),
    PermissionDefinition("void_payments", "Void payments using audited reversal entries", "Sensitive Financial Actions"),
    PermissionDefinition("manage_cheques", "Clear, bounce, or cancel cheques", "Sensitive Financial Actions"),
    PermissionDefinition("view_audit_log", "View the audit log", "Audit"),
    PermissionDefinition("manage_timetable", "Manage teachers, subjects, timetable setup", "Timetable"),
    PermissionDefinition("generate_timetable", "Generate and publish timetables", "Timetable"),
    PermissionDefinition("view_timetable", "View and print timetables", "Timetable", True),
)

PERMISSION_KEYS = frozenset(item.key for item in PERMISSIONS)
DEFAULT_ACCOUNTANT_PERMISSIONS = frozenset(
    item.key for item in PERMISSIONS if item.default_for_accountant
)


def permission_definition(key: str) -> PermissionDefinition | None:
    """Return catalog metadata for a permission key."""
    return next((item for item in PERMISSIONS if item.key == key), None)
