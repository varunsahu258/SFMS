"""Main BIG-register fee collection window for SFMS."""

from __future__ import annotations

from ui_collection_common import CollectionBaseWindow


class CollectionMainWindow(CollectionBaseWindow):
    """Collect BIG and BOTH register fees for the selected student."""

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"
