"""Pure-Tk date selector used by SFMS date inputs."""

from __future__ import annotations

import calendar
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from ui_theme import apply_theme

DATE_FORMAT = "%d-%m-%Y"


class DatePickerDialog(tk.Toplevel):
    """Small calendar dialog that returns a DD-MM-YYYY date."""

    def __init__(self, master, initial: str = ""):
        super().__init__(master)
        apply_theme(self)
        self.title("Select Date")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result: str | None = None
        try:
            selected = datetime.strptime(initial, DATE_FORMAT)
        except ValueError:
            selected = datetime.today()
        self.month = tk.IntVar(value=selected.month)
        self.year = tk.IntVar(value=selected.year)
        self._selected_day = selected.day
        header = ttk.Frame(self, padding=(12, 10)); header.pack(fill="x")
        ttk.Button(header, text="‹", command=lambda: self._move(-1)).pack(side="left")
        self.heading = ttk.Label(header, text="", anchor="center")
        self.heading.pack(side="left", fill="x", expand=True, padx=12)
        ttk.Button(header, text="›", command=lambda: self._move(1)).pack(side="right")
        self.days = ttk.Frame(self, padding=(10, 0, 10, 10)); self.days.pack()
        self._render()
        self.wait_window(self)

    def _move(self, amount: int) -> None:
        month = self.month.get() + amount
        year = self.year.get()
        if month < 1: month, year = 12, year - 1
        elif month > 12: month, year = 1, year + 1
        self.month.set(month); self.year.set(year); self._render()

    def _render(self) -> None:
        for child in self.days.winfo_children(): child.destroy()
        self.heading.configure(text=f"{calendar.month_name[self.month.get()]} {self.year.get()}")
        for column, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ttk.Label(self.days, text=name, width=4, anchor="center").grid(row=0, column=column, pady=(0, 4))
        for row_index, week in enumerate(calendar.monthcalendar(self.year.get(), self.month.get()), start=1):
            for column, day in enumerate(week):
                if day:
                    ttk.Button(self.days, text=str(day), width=4,
                               command=lambda value=day: self._choose(value)).grid(row=row_index, column=column, padx=1, pady=1)

    def _choose(self, day: int) -> None:
        self.result = datetime(self.year.get(), self.month.get(), day).strftime(DATE_FORMAT)
        self.destroy()


class DateEntry(ttk.Frame):
    """Readonly date entry with a calendar selector button."""

    def __init__(self, master, *, textvariable: tk.StringVar, width: int = 13, state: str = "normal", **kwargs):
        super().__init__(master, **kwargs)
        self.variable = textvariable
        self.entry = ttk.Entry(self, textvariable=textvariable, width=width, state="readonly")
        self.entry.pack(side="left", fill="x", expand=True)
        self.button = ttk.Button(self, text="📅", width=3, command=self.choose)
        self.button.pack(side="left", padx=(3, 0))
        self.configure_state(state)

    def choose(self) -> None:
        dialog = DatePickerDialog(self.winfo_toplevel(), self.variable.get())
        if dialog.result:
            self.variable.set(dialog.result)

    def configure_state(self, state: str) -> None:
        disabled = str(state) == "disabled"
        self.entry.configure(state="disabled" if disabled else "readonly")
        self.button.configure(state="disabled" if disabled else "normal")

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        result = super().configure(cnf, **kwargs)
        if state is not None:
            self.configure_state(state)
        return result
