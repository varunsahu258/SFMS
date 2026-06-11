"""Regression checks for first-run setup navigation and save controls."""

from __future__ import annotations

import inspect


def test_prelogin_setup_has_a_fixed_visible_create_action():
    from ui_first_time_setup import FirstTimeSetupWindow

    source = inspect.getsource(FirstTimeSetupWindow.__init__)
    assert 'self.rowconfigure(0, weight=1)' in source
    assert 'footer.grid(row=1, column=0, sticky="ew")' in source
    assert 'text="Create Administrator and Continue"' in source
    assert 'style="Accent.TButton"' in source
    assert 'self.bind("<Return>"' in source


def test_postlogin_wizard_keeps_navigation_in_a_fixed_footer():
    from ui_setup_wizard import SetupWizardWindow

    source = inspect.getsource(SetupWizardWindow.__init__)
    show_source = inspect.getsource(SetupWizardWindow.show_step)
    assert 'self.navigation.grid(row=1, column=0, sticky="ew")' in source
    assert 'self.content.grid(row=0, column=0, sticky="nsew")' in source
    assert 'style="Accent.TButton"' in source
    assert 'text="Save Setup and Continue" if self.step == 4 else "Next"' in show_source
    assert 'Step {self.step + 1} of 5' in show_source


def test_setup_save_failure_is_shown_without_closing_wizard():
    from ui_setup_wizard import SetupWizardWindow

    source = inspect.getsource(SetupWizardWindow.finish)
    assert 'self.next_button.configure(state="disabled")' in source
    assert 'except Exception as exc:' in source
    assert 'self.next_button.configure(state="normal")' in source
    assert 'messagebox.showerror("Setup could not be saved"' in source
