from __future__ import annotations

from django import forms

from app.core.widgets import EstonianDateField
from app.notifications.services import REASON_LIMIT


class DeferForm(forms.Form):
    until = EstonianDateField(label="Lükka edasi kuni")


class DismissForm(forms.Form):
    reason = forms.CharField(
        label="Miks see meeldetuletus suletakse?",
        max_length=REASON_LIMIT,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
