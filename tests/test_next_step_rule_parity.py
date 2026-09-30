"""One next-step rule, three panels, the differences kept on purpose (ENG-127).

`NextActionForm` (PRAEGUNE TEGEVUS), `ComposerForm` (the compatibility save) and
`MatterProgressForm` (+ Märge) each restated «a date with no sentence is refused
on the sentence». The rule now lives once (`forms.clean_next_step_sentence`),
and the table below pins what each panel does with the same five inputs, so a
change to the shared rule shows up in all three and a difference between them
is visibly deliberate:

* `NextActionForm` *is* the step, so a missing sentence is always refused;
* the other two carry a step as an extra, so both boxes empty is «no step».

`MatterProgressForm` has had no step boxes of its own since docs/adr/0124: its
step is the activity itself — `Tegevus` on a day after today with `Märgi
järgmiseks tegevuseks` ticked — so its «sentence» is `title` and its «date» is
that ticked day ahead. The rule is the same call on the same refusal.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.matters.forms import ComposerForm, MatterProgressForm, NextActionForm
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

REFUSAL = "Kirjuta järgmine tegevus."
SOON = timezone.localdate() + timedelta(days=5)

#: (panel, input) -> refused on the sentence?
EXPECTED = {
    ("next_action", "empty"): True,
    ("next_action", "sentence"): False,
    ("next_action", "date"): True,
    ("next_action", "sentence+date"): False,
    ("next_action", "sentence+period"): False,
    ("composer", "empty"): False,
    ("composer", "sentence"): False,
    ("composer", "date"): True,
    ("composer", "sentence+date"): False,
    ("progress", "empty"): False,
    ("progress", "sentence"): False,
    ("progress", "date"): True,
    ("progress", "sentence+date"): False,
}


def _next_action(shape):
    data = {
        "empty": {},
        "sentence": {"text": "Vaatan eelnõu üle"},
        "date": {"target_date": SOON.isoformat()},
        "sentence+date": {"text": "Vaatan eelnõu üle", "target_date": SOON.isoformat()},
        "sentence+period": {
            "text": "Vaatan eelnõu üle",
            "next_precision": DatePrecision.MONTH,
            "next_month": str(SOON.month),
            "next_year": str(SOON.year),
        },
    }[shape]
    form = NextActionForm(data, periods=True)
    return form, "text"


def _composer(shape, matter, viewer):
    data = {"body": "Ministeerium helistas."}
    data.update(
        {
            "empty": {},
            "sentence": {"next_text": "Vaatan eelnõu üle"},
            "date": {"next_date": SOON.isoformat()},
            "sentence+date": {"next_text": "Vaatan eelnõu üle", "next_date": SOON.isoformat()},
        }[shape]
    )
    return ComposerForm(data, matter=matter, viewer=viewer), "next_text"


def _progress(shape):
    today = timezone.localdate().isoformat()
    data = {
        # No sentence and no step: a stage-free, file-free press is refused as
        # empty, but not on the sentence, which is the only thing asked here.
        "empty": {"occurred_on": today},
        # A sentence on a day that is not ahead: the box is inert.
        "sentence": {"title": "Vaatan eelnõu üle", "occurred_on": today, "as_next_step": "on"},
        "date": {"occurred_on": SOON.isoformat(), "as_next_step": "on"},
        "sentence+date": {
            "title": "Vaatan eelnõu üle",
            "occurred_on": SOON.isoformat(),
            "as_next_step": "on",
        },
    }[shape]
    return MatterProgressForm(data), "title"


@pytest.mark.parametrize(("panel", "shape"), sorted(EXPECTED))
def test_each_panel_applies_the_rule_as_the_table_says(specialist, panel, shape):
    matter = factories.MatterFactory(owner=specialist)
    if panel == "next_action":
        form, field = _next_action(shape)
    elif panel == "composer":
        form, field = _composer(shape, matter, specialist)
    else:
        form, field = _progress(shape)

    form.is_valid()
    refused = REFUSAL in form.errors.get(field, [])
    assert refused is EXPECTED[(panel, shape)], (panel, shape, form.errors)
    if not refused:
        # Nothing else about the step refused it either.
        assert field not in form.errors, (panel, shape, form.errors)


def test_the_composer_reads_both_empty_as_no_step(specialist):
    matter = factories.MatterFactory(owner=specialist)
    form, _ = _composer("empty", matter, specialist)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["next_action_kwargs"] is None
