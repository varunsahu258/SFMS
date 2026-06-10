# conftest.py  –  place in SFMS-main/ (project root)
import sys
import types
from pathlib import Path

# Add project root to sys.path so tests can import source modules directly.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Comprehensive tkinter stub
# Many source modules import tkinter at the top level (auth, integrity,
# receipt_printing, ui_login, ui_settings, ui_theme, …).  We replace the
# whole package before any test module is collected so those imports succeed
# on headless / no-display machines without any real Tk installation.
# ---------------------------------------------------------------------------

def _make_tk_stub():
    tk = types.ModuleType("tkinter")

    # Base widget that accepts and ignores every common call
    class _Widget:
        def __init__(self, *a, **kw): pass
        def pack(self, *a, **kw): pass
        def grid(self, *a, **kw): pass
        def place(self, *a, **kw): pass
        def config(self, *a, **kw): pass
        configure = config
        def destroy(self, *a, **kw): pass
        def after(self, *a, **kw): pass
        def after_cancel(self, *a, **kw): pass
        def winfo_exists(self): return 0
        def winfo_screenwidth(self): return 1920
        def winfo_screenheight(self): return 1080
        def winfo_reqwidth(self): return 0
        def winfo_reqheight(self): return 0
        def winfo_width(self): return 0
        def winfo_height(self): return 0
        def update(self, *a, **kw): pass
        def update_idletasks(self, *a, **kw): pass
        def bind(self, *a, **kw): pass
        def unbind(self, *a, **kw): pass
        def focus_set(self, *a, **kw): pass
        def grab_set(self, *a, **kw): pass
        def grab_release(self, *a, **kw): pass
        def transient(self, *a, **kw): pass
        def resizable(self, *a, **kw): pass
        def protocol(self, *a, **kw): pass
        def geometry(self, *a, **kw): pass
        def withdraw(self, *a, **kw): pass
        def deiconify(self, *a, **kw): pass
        def lift(self, *a, **kw): pass
        def title(self, *a, **kw): pass
        def columnconfigure(self, *a, **kw): pass
        def rowconfigure(self, *a, **kw): pass
        def cget(self, *a, **kw): return ""
        def keys(self): return []
        def wait_window(self, *a, **kw): pass
        def mainloop(self, *a, **kw): pass
        def quit(self, *a, **kw): pass
        def nametowidget(self, *a, **kw): return self
        def event_generate(self, *a, **kw): pass
        def selection_clear(self, *a, **kw): pass
        def selection_set(self, *a, **kw): pass
        def delete(self, *a, **kw): pass
        def insert(self, *a, **kw): pass
        def get(self, *a, **kw): return ""
        def see(self, *a, **kw): pass
        def heading(self, *a, **kw): pass
        def column(self, *a, **kw): pass
        def tag_configure(self, *a, **kw): pass
        def yview(self, *a, **kw): pass
        def xview(self, *a, **kw): pass

    class Tk(_Widget):
        _default_root = None
        def __init__(self, *a, **kw):
            Tk._default_root = self

    class Toplevel(_Widget):
        pass

    class Frame(_Widget): pass
    class LabelFrame(_Widget): pass
    class Label(_Widget): pass
    class Button(_Widget): pass
    class Entry(_Widget): pass
    class Text(_Widget): pass
    class Canvas(_Widget): pass
    class Listbox(_Widget): pass
    class Checkbutton(_Widget): pass
    class Radiobutton(_Widget): pass
    class Scale(_Widget): pass
    class Spinbox(_Widget): pass
    class Scrollbar(_Widget): pass
    class Panedwindow(_Widget): pass
    class OptionMenu(_Widget): pass
    class Menu(_Widget): pass
    class Misc(_Widget): pass

    class StringVar:
        def __init__(self, *a, **kw): self._v = kw.get("value", "")
        def get(self): return self._v
        def set(self, v): self._v = v
        def trace_add(self, *a, **kw): pass
        def trace(self, *a, **kw): pass

    class IntVar(StringVar):
        def __init__(self, *a, **kw): self._v = kw.get("value", 0)

    class BooleanVar(StringVar):
        def __init__(self, *a, **kw): self._v = kw.get("value", False)

    class DoubleVar(StringVar):
        def __init__(self, *a, **kw): self._v = kw.get("value", 0.0)

    class TclError(Exception):
        pass

    # tkinter.font stub
    _font = types.ModuleType("tkinter.font")
    _font.families = lambda root=None: ("Arial", "Segoe UI", "Helvetica")
    class _Font:
        def __init__(self, *a, **kw): pass
        def actual(self, *a, **kw): return {}
        def cget(self, *a, **kw): return 10
        def configure(self, *a, **kw): pass
        def measure(self, *a, **kw): return 0
    _font.Font = _Font
    _font.nametofont = lambda name: _Font()

    # tkinter.ttk stub
    _ttk = types.ModuleType("tkinter.ttk")
    class _TtkWidget(_Widget):
        pass
    for _n in ("Frame", "Label", "Button", "Entry", "Combobox", "Treeview",
               "Scrollbar", "Notebook", "LabelFrame", "Progressbar",
               "Separator", "Checkbutton", "Radiobutton", "Scale",
               "Spinbox", "PanedWindow", "Sizegrip"):
        setattr(_ttk, _n, type(_n, (_TtkWidget,), {}))

    class _Style:
        def __init__(self, *a, **kw): pass
        def theme_use(self, *a, **kw): pass
        def configure(self, *a, **kw): pass
        def map(self, *a, **kw): pass
        def lookup(self, *a, **kw): return ""
        def layout(self, *a, **kw): return []
    _ttk.Style = _Style

    # messagebox stub
    _mb = types.ModuleType("tkinter.messagebox")
    _mb.showinfo    = lambda *a, **kw: None
    _mb.showwarning = lambda *a, **kw: None
    _mb.showerror   = lambda *a, **kw: None
    _mb.askyesno    = lambda *a, **kw: True
    _mb.askokcancel = lambda *a, **kw: True
    _mb.askyesnocancel = lambda *a, **kw: True

    # filedialog stub
    _fd = types.ModuleType("tkinter.filedialog")
    _fd.askopenfilename  = lambda *a, **kw: ""
    _fd.asksaveasfilename = lambda *a, **kw: ""
    _fd.askdirectory     = lambda *a, **kw: ""
    _fd.askopenfilenames = lambda *a, **kw: ()

    # simpledialog stub
    _sd = types.ModuleType("tkinter.simpledialog")
    _sd.askstring  = lambda *a, **kw: None
    _sd.askinteger = lambda *a, **kw: None
    _sd.askfloat   = lambda *a, **kw: None

    # Populate the main tk module
    for _cls in (Tk, Toplevel, Frame, LabelFrame, Label, Button, Entry,
                 Text, Canvas, Listbox, Checkbutton, Radiobutton, Scale,
                 Spinbox, Scrollbar, Panedwindow, OptionMenu, Menu, Misc,
                 StringVar, IntVar, BooleanVar, DoubleVar, TclError):
        setattr(tk, _cls.__name__, _cls)

    tk.ttk        = _ttk
    tk.messagebox = _mb
    tk.filedialog = _fd
    tk.simpledialog = _sd
    tk.font       = _font
    tk._default_root = None

    # Common constants
    for _k, _v in dict(
        END="end", INSERT="insert", DISABLED="disabled", NORMAL="normal",
        ACTIVE="active", HIDDEN="hidden",
        LEFT="left", RIGHT="right", TOP="top", BOTTOM="bottom",
        BOTH="both", X="x", Y="y", NONE="none",
        YES=True, NO=False, TRUE=True, FALSE=False,
        WORD="word", CHAR="char",
        FLAT="flat", RAISED="raised", SUNKEN="sunken", GROOVE="groove", RIDGE="ridge",
        HORIZONTAL="horizontal", VERTICAL="vertical",
        BROWSE="browse", MULTIPLE="multiple", EXTENDED="extended", SINGLE="single",
        NW="nw", N="n", NE="ne", W="w", E="e", SW="sw", S="s", SE="se", CENTER="center",
        CURRENT="current", LAST="last", ALL="all", FIRST="first",
        EW="ew", NS="ns", NSEW="nsew",
    ).items():
        setattr(tk, _k, _v)

    sys.modules["tkinter"]            = tk
    sys.modules["tkinter.ttk"]        = _ttk
    sys.modules["tkinter.messagebox"] = _mb
    sys.modules["tkinter.filedialog"] = _fd
    sys.modules["tkinter.simpledialog"] = _sd
    sys.modules["tkinter.font"]       = _font

_make_tk_stub()
