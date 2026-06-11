"""Reusable embedded-page support for SFMS desktop modules."""

from __future__ import annotations

import tkinter as tk


class WorkspacePage(tk.Frame):
    """Render a primary module either inside the dashboard or in a legacy window.

    Primary navigation uses ``embedded=True``.  Existing direct call sites can keep
    constructing the same class without that flag and receive a normal Toplevel.
    Short-lived confirmation and data-entry dialogs intentionally remain Toplevels.
    """

    def __init__(self, master=None, *, embedded: bool = False, **kwargs):
        self._embedded = bool(embedded)
        self._standalone_window: tk.Toplevel | None = None
        parent = master
        if not self._embedded:
            self._standalone_window = tk.Toplevel(master)
            parent = self._standalone_window
        super().__init__(parent, **kwargs)
        from ui_theme import apply_theme

        theme_target = self._standalone_window or self.winfo_toplevel()
        self.language, self.ui_font = apply_theme(theme_target)
        palette = theme_target._sfms_palette
        self.configure(bg=palette["bg"])
        if not self._embedded:
            self.pack(fill="both", expand=True)

    @property
    def embedded(self) -> bool:
        """Return whether this page is hosted by the dashboard workspace."""
        return self._embedded

    def _window_call(self, method: str, *args):
        """Delegate window-manager operations only for standalone pages."""
        if self._standalone_window is None:
            return None
        return getattr(self._standalone_window, method)(*args)

    def title(self, text: str | None = None):
        return self._window_call("title", text) if text is not None else self._window_call("title")

    def geometry(self, value: str | None = None):
        return self._window_call("geometry", value) if value is not None else self._window_call("geometry")

    def minsize(self, width: int, height: int):
        return self._window_call("minsize", width, height)

    def maxsize(self, width: int, height: int):
        return self._window_call("maxsize", width, height)

    def resizable(self, width: bool | None = None, height: bool | None = None):
        if width is None or height is None:
            return self._window_call("resizable")
        return self._window_call("resizable", width, height)

    def protocol(self, name: str | None = None, func=None):
        if name is None:
            return self._window_call("protocol")
        return self._window_call("protocol", name, func)

    def transient(self, master=None):
        target = getattr(master, "_standalone_window", None) or master
        return self._window_call("transient", target)

    def grab_set(self):
        return self._window_call("grab_set")

    def grab_release(self):
        return self._window_call("grab_release")

    def state(self, value: str | None = None):
        return self._window_call("state", value) if value is not None else self._window_call("state")

    def deiconify(self):
        return self._window_call("deiconify")

    def withdraw(self):
        return self._window_call("withdraw")

    def lift(self, above_this=None):
        if self._standalone_window is not None:
            return self._standalone_window.lift(above_this)
        return super().lift(above_this)

    def destroy(self):
        """Destroy this page and its compatibility window, when present."""
        window = self._standalone_window
        self._standalone_window = None
        scheduler = self.master
        return_to_dashboard = (
            self._embedded
            and not getattr(self, "_workspace_navigating", False)
            and getattr(self, "_workspace_on_close", None)
        )
        try:
            super().destroy()
        finally:
            if window is not None:
                try:
                    if window.winfo_exists():
                        window.destroy()
                except tk.TclError:
                    pass
            elif return_to_dashboard:
                try:
                    scheduler.after_idle(return_to_dashboard)
                except tk.TclError:
                    pass
