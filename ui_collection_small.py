"""SMALL-register fee collection window for SFMS."""

from __future__ import annotations

from ui_collection_common import CollectionBaseWindow


class CollectionSmallWindow(CollectionBaseWindow):
    """Collect SMALL and BOTH register fees with a compact visible fee list."""

    register_types = ("SMALL", "BOTH")
    receipt_type = "SMALL"
    max_rows = 4
