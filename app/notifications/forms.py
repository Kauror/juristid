from __future__ import annotations

from django import forms

from app.core.widgets import EstonianDateField


class DeferForm(forms.Form):
    until = EstonianDateField(label="Lükka edasi kuni")
