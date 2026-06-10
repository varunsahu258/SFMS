"""Offline searchable help viewer for SFMS."""

from __future__ import annotations

import html
import importlib
import importlib.util
import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

HELP_FILE = Path(__file__).resolve().parent / "assets" / "help" / "index.html"
SECTIONS = (("Getting Started", "getting-started"), ("Collections", "collections"), ("Reports", "reports"), ("Backups", "backups"), ("Security", "security"), ("Shortcuts", "shortcuts"))


class HelpWindow(tk.Toplevel):
    """Display bundled HTML help, with a Text fallback when tkinterweb is absent."""

    def __init__(self, master=None):
        super().__init__(master)
        self.title("SFMS Help")
        self.geometry("1000x700")
        self.search_var = tk.StringVar()
        self.html_source = HELP_FILE.read_text(encoding="utf-8")
        self._build()

    def _build(self) -> None:
        search = ttk.Frame(self, padding=8)
        search.pack(fill="x")
        ttk.Label(search, text="Search Help").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(search, text="Search", command=self.search).pack(side="left")
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        toc = ttk.Frame(body, padding=8)
        body.add(toc, weight=1)
        ttk.Label(toc, text="Contents", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        for title, anchor in SECTIONS:
            ttk.Button(toc, text=title, command=lambda value=anchor: self.go_to(value)).pack(fill="x", pady=2)
        viewer = ttk.Frame(body)
        body.add(viewer, weight=4)
        if importlib.util.find_spec("tkinterweb") is not None:
            module = importlib.import_module("tkinterweb")
            self.web = module.HtmlFrame(viewer)
            self.web.pack(fill="both", expand=True)
            self.text = None
            self.web.load_html(self.html_source, base_url=HELP_FILE.parent.as_uri() + "/")
        else:
            self.web = None
            self.text = tk.Text(viewer, wrap="word", padx=15, pady=15)
            scrollbar = ttk.Scrollbar(viewer, command=self.text.yview)
            self.text.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            self.text.pack(fill="both", expand=True)
            plain = re.sub(r"<[^>]+>", "", self.html_source)
            self.text.insert("1.0", html.unescape(plain))
            self.text.configure(state="disabled")
            self.text.tag_configure("match", foreground="red", background="#ffe0e0")
        entry.bind("<Return>", lambda _event: self.search())

    def go_to(self, anchor: str) -> None:
        if self.web is not None:
            self.web.load_url(f"{HELP_FILE.as_uri()}#{anchor}")
            return
        heading = next(title for title, value in SECTIONS if value == anchor)
        position = self.text.search(heading, "1.0", nocase=True)
        if position:
            self.text.see(position)

    def search(self) -> None:
        term = self.search_var.get().strip()
        if self.web is not None:
            source = self.html_source
            if term:
                pattern = re.compile(re.escape(term), re.IGNORECASE)
                source = pattern.sub(lambda match: f'<span style="color:red;background:#ffe0e0">{match.group(0)}</span>', source)
            self.web.load_html(source, base_url=HELP_FILE.parent.as_uri() + "/")
            return
        self.text.configure(state="normal")
        self.text.tag_remove("match", "1.0", "end")
        if term:
            start = "1.0"
            while True:
                start = self.text.search(term, start, stopindex="end", nocase=True)
                if not start:
                    break
                end = f"{start}+{len(term)}c"
                self.text.tag_add("match", start, end)
                start = end
            ranges = self.text.tag_ranges("match")
            if ranges:
                self.text.see(ranges[0])
        self.text.configure(state="disabled")
