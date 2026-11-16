from __future__ import annotations

from django import forms

from app.core.widgets import EstonianDateField


class DeferForm(forms.Form):
    until = EstonianDateField(label="Lükka edasi kuni")


class PersonalReminderForm(forms.Form):
    """«Lisa meeldetuletus»: a day, and a short note if wanted."""

    remind_on = EstonianDateField(label="Kuupäev")
    note = forms.CharField(label="Märkus", max_length=200, required=False)
