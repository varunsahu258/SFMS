"""Exemption-aware fee collection window for SFMS."""

from __future__ import annotations

from ui_collection_common import CollectionBaseWindow


class CollectionExemptionWindow(CollectionBaseWindow):
    """Collect fees while greying out fee heads covered by exemption records."""

    register_types = ("BIG", "BOTH")
    receipt_type = "BIG"
    force_exemption_view = True
