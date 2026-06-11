"""Exemption-aware fee collection window for SFMS."""

from __future__ import annotations

import auth

from ui_collection_common import CollectionBaseWindow


class CollectionExemptionWindow(CollectionBaseWindow):
    """Collect fees while greying out fee heads covered by exemption records."""

    @auth.require_permission("collect_exemption_fees")
    def __init__(self, master=None, *, embedded: bool = False):
        super().__init__(master, embedded=embedded)

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"
    force_exemption_view = True
