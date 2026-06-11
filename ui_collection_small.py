"""SMALL-register fee collection window for SFMS."""

from __future__ import annotations

import auth

from ui_collection_common import CollectionBaseWindow


class CollectionSmallWindow(CollectionBaseWindow):
    """Collect SMALL and BOTH register fees with a compact visible fee list."""

    @auth.require_permission("collect_small_fees")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)

    register_types = ("SMALL", "BOTH")
    receipt_type = "SMALL"
    max_rows = 4
