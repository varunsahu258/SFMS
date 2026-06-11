"""Central English and Hindi labels used by SFMS user-interface shells."""

LABELS = {
    "en": {
        "login": "Login", "username": "Username", "password": "Password", "show": "Show",
        "dashboard": "Dashboard", "main_collection": "Main Collection", "dues": "Dues",
        "reports": "Reports", "students": "Students", "backup": "Backup", "settings": "Settings",
        "users": "Users", "help": "Help", "about": "About", "logout": "Logout",
        "small_collection": "Small Collection", "exemption_collection": "Exemption Collection",
        "advance_payment": "Advance Payment", "discounts": "Discounts", "exemptions": "Exemptions",
        "fee_heads": "Fee Heads", "fee_structure": "Fee Structure", "academic_years": "Academic Years",
        "receipt_reprint": "Receipt Reprint", "void_payment": "Void Payment", "audit_log": "Audit Log",
        "fee_notices": "Fee Notices", "user_management": "User Management", "change_password": "Change Password",
        "save": "Save", "cancel": "Cancel", "general": "General", "appearance": "Appearance",
        "security": "Security", "data": "Data", "language": "Language", "theme": "Theme",
        "light": "Light", "dark": "Dark", "english": "English", "hindi": "Hindi",
        "timetable": "Timetable", "timetable_setup": "Timetable Setup",
        "generate_timetable": "Generate Timetable", "view_timetable": "View Timetable",
    },
    "hi": {
        "login": "लॉगिन", "username": "उपयोगकर्ता नाम", "password": "पासवर्ड", "show": "दिखाएँ",
        "dashboard": "डैशबोर्ड", "main_collection": "मुख्य शुल्क संग्रह", "dues": "बकाया",
        "reports": "रिपोर्ट", "students": "विद्यार्थी", "backup": "बैकअप", "settings": "सेटिंग्स",
        "users": "उपयोगकर्ता", "help": "सहायता", "about": "परिचय", "logout": "लॉगआउट",
        "small_collection": "लघु शुल्क संग्रह", "exemption_collection": "छूट शुल्क संग्रह",
        "advance_payment": "अग्रिम भुगतान", "discounts": "रियायत", "exemptions": "छूट",
        "fee_heads": "शुल्क मद", "fee_structure": "शुल्क संरचना", "academic_years": "शैक्षणिक वर्ष",
        "receipt_reprint": "रसीद पुनर्मुद्रण", "void_payment": "भुगतान निरस्तीकरण", "audit_log": "ऑडिट लॉग",
        "fee_notices": "शुल्क सूचना", "user_management": "उपयोगकर्ता प्रबंधन", "change_password": "पासवर्ड बदलें",
        "save": "सहेजें", "cancel": "रद्द करें", "general": "सामान्य", "appearance": "रूप-रंग",
        "security": "सुरक्षा", "data": "डेटा", "language": "भाषा", "theme": "थीम",
        "light": "हल्का", "dark": "गहरा", "english": "अंग्रेज़ी", "hindi": "हिन्दी",
        "timetable": "समय-सारणी", "timetable_setup": "समय-सारणी व्यवस्था",
        "generate_timetable": "समय-सारणी बनाएँ", "view_timetable": "समय-सारणी देखें",
    },
}


def label(key: str, language: str = "en") -> str:
    """Return a localized label with an English fallback."""
    return LABELS.get(language, LABELS["en"]).get(key, LABELS["en"].get(key, key))
