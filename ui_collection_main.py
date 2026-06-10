"""Main BIG-register fee collection window for SFMS."""

from __future__ import annotations

import auth

from ui_collection_common import CollectionBaseWindow


class CollectionMainWindow(CollectionBaseWindow):
    """Collect BIG and BOTH register fees for the selected student."""

    @auth.require_permission("collect_main_fees")
    def __init__(self, master=None):
        super().__init__(master)

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"
