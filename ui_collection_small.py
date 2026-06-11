"""SMALL-register fee collection using the Main Collection workflow."""

from __future__ import annotations

import auth

from ui_collection_main import CollectionMainWindow


class CollectionSmallWindow(CollectionMainWindow):
    """Use identical head selection and payment controls for the small register."""

    register_types = ("SMALL", "BOTH")
    receipt_type = "SMALL"
    page_title = "Small Register Fee Collection"
    collection_note = "SMALL COLLECTION"

    @auth.require_permission("collect_small_fees")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)
