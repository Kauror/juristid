"""Retired user-facing controls stay retired — product contracts, not cleanup.

docs/adr/0155: «A refactor or consolidation may not resurrect a retired
user-facing control merely because the underlying backend capability still
exists. Reintroducing a retired control requires an explicit new product-owner
decision.» Removed UI has come back more than once during consolidation work,
because the capability behind it was still there and a partial still drew it.
Each test here names one control or sentence the owner retired, and the
decision that retired it.

**The capability is not what is tested.** `Matter.track` «Koja algatus»,
`MatterProceduralLink`, the `Hetkeseis` prefill, related-Teema linking and
`Teemaviide` all still exist and are tested where they live; what these tests
pin is that the *control* or *sentence* does not reappear on the surface it
was taken off. Do not delete backend capability to make one of them pass, and
do not delete one of them to bring a control back: that takes a new decision.

The browser half — what appears only with scripting, and the layout order — is
e2e/test_koda_proposal_create_flow.py.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.matters.models import Matter, MatterProceduralLink
from app.taxonomy.models import LegalInstrumentType
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")

#: The owner's decision of 2026-10-12 (docs/adr/0151 and 0130 amendments).
RETIRED_ON_UUS_TEEMA = (
    'name="koda_initiative"',
    "Koda algatab selle ise: saabumise kuupäeva ega arvamuse tähtaega ei ole.",
    'name="menetlus-url"',
    'id="menetluse-link"',
    "Hetkeseis valiti õigusakti järgi — muuda, kui teema on mujal.",
    "Seos luuakse koos teemaga. Võid valida ka teema, mida soovitused ei paku.",
)


def instrument(key: str) -> str:
    return str(LegalInstrumentType.objects.get(key=key).pk)


def pdf(name: str = "Pöördumine.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf(["Koja pöördumine"]), "application/pdf")


def sent_section(body: str) -> str:
    """The opening tag of «Märgi saadetud Koja arvamuseks»'s section."""
    return body.split('id="saadetud-koja-arvamus"', 1)[1].split(">", 1)[0]


# ---------------------------------------------------------------------------
# Uus teema — what it no longer asks or says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retired", RETIRED_ON_UUS_TEEMA)
def test_uus_teema_does_not_render_a_retired_control(signed_in, retired):
    body = signed_in.get(CREATE).content.decode()
    assert retired not in body


@pytest.mark.parametrize("retired", RETIRED_ON_UUS_TEEMA)
def test_a_refused_uus_teema_does_not_render_one_either(signed_in, retired):
    """A refusal re-renders the form bound — the path a resurrected control
    most often hides on."""
    response = signed_in.post(
        CREATE, {"title": "", "legal_instruments": [instrument("koja-ettepanek")]}
    )
    assert response.status_code == 400
    assert retired not in response.content.decode()


def test_uus_teema_has_no_menetluse_link_label_at_all(signed_in):
    body = signed_in.get(CREATE).content.decode()
    form = body.split('<form class="form createform"', 1)[1].split("</form>", 1)[0]
    assert "Menetluse link" not in re.sub(r"\{#.*?#\}", "", form)


def test_a_forged_menetluse_link_creates_no_link(signed_in):
    """The control is gone, and so is the write: a crafted `menetlus-url`
    binds to nothing (the capability stays on the Teema page)."""
    signed_in.post(
        CREATE, {"title": "Võltsitud link", "menetlus-url": "https://eelnoud.valitsus.ee/x"}
    )
    matter = Matter.objects.get(title="Võltsitud link")
    assert not MatterProceduralLink.objects.filter(matter=matter).exists()


def test_muuda_teemat_has_no_initiative_checkbox(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    assert 'name="koda_initiative"' not in body


# ---------------------------------------------------------------------------
# «Märgi saadetud Koja arvamuseks» — only where it makes sense
# ---------------------------------------------------------------------------


def test_the_sent_box_is_not_offered_on_a_fresh_form(signed_in):
    body = signed_in.get(CREATE).content.decode()
    assert "hidden" in sent_section(body)


def test_the_sent_box_is_not_offered_for_an_ordinary_instrument(signed_in, evidence_root):
    response = signed_in.post(
        CREATE, {"title": "", "legal_instruments": [instrument("seadus")], "files": [pdf()]}
    )
    assert response.status_code == 400
    assert "hidden" in sent_section(response.content.decode())


def test_the_sent_box_is_not_offered_before_a_file_exists(signed_in):
    response = signed_in.post(
        CREATE, {"title": "", "legal_instruments": [instrument("koja-ettepanek")]}
    )
    assert response.status_code == 400
    assert "hidden" in sent_section(response.content.decode())


def test_the_sent_box_is_offered_for_the_proposal_instrument_with_a_file(signed_in, evidence_root):
    response = signed_in.post(
        CREATE, {"title": "", "legal_instruments": [instrument("koja-ettepanek")], "files": [pdf()]}
    )
    body = response.content.decode()
    assert response.status_code == 400
    assert "hidden" not in sent_section(body)
    assert "Märgi saadetud Koja arvamuseks" in body


# ---------------------------------------------------------------------------
# Uus teema — the action bar ends the form
# ---------------------------------------------------------------------------


def test_salvesta_comes_after_the_related_teema_section_and_ends_the_form(signed_in):
    body = signed_in.get(CREATE).content.decode()
    form = body.split('<form class="form createform"', 1)[1].split("</form>", 1)[0]
    form = re.sub(r"\{%.*?%\}|<!--.*?-->", "", form, flags=re.S)
    related = form.index('id="seo-olemasoleva-teemaga"')
    sent = form.index('id="saadetud-koja-arvamus"')
    files = form.index('id="failid"')
    actions = form.index("createform__actions")
    assert files < sent < related < actions
    tail = form[actions:]
    assert tail.index(">Loobu</a>") < tail.index(">Salvesta</button>")
    # Nothing but the action bar after it: its closing tag is the form's last.
    assert re.fullmatch(r"\s*", tail.split(">Salvesta</button>", 1)[1].replace("</div>", "", 1))
    assert "createform__actions--end" in tail


# ---------------------------------------------------------------------------
# The Teema header — Teemaviide moved, Kopeeri gone
# ---------------------------------------------------------------------------


def test_teemaviide_is_under_teema_andmed_and_not_in_the_header(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    assert matter.display_reference

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    head = body.split('id="teema-pais"', 1)[1].split("</header>", 1)[0]
    rail = body.split('id="teema-andmed"', 1)[1]
    assert ">Teemaviide<" not in head
    assert 'id="teemaviide"' not in head
    assert 'aria-label="Kopeeri teemaviide"' not in body
    assert 'data-copy-from="teemaviide"' not in body
    row = rail.split('<span class="railcard__key">Teemaviide</span>', 1)[1].split("</div>", 1)[0]
    assert matter.display_reference in row
    assert "Kopeeri" not in row


# ---------------------------------------------------------------------------
# «Jõustuvad aktid» — retired as a destination by docs/adr/0071
#
# The owner's decision of 2026-10-13 (docs/adr/0158) brings the commencement
# calendar and list back *as a presentation of the Teemad register*, reachable
# only from its Jõustumine filters. It explicitly does not restore the page:
# the retired address keeps redirecting to the register, and nothing on the bar
# names it. These pin that distinction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/jalgimine/joustumised/", "/joustuvad-aktid/"])
def test_the_retired_commencement_page_still_redirects_to_the_register(signed_in, path):
    response = signed_in.get(path)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('matters:matter_list')}?joustumine=on&olek=koik"


@pytest.mark.parametrize("query", ["", "?joustumine=on&vaade=kalender"])
def test_no_commencement_destination_is_on_the_bar(signed_in, query):
    body = signed_in.get(f"{reverse('matters:matter_list')}{query}").content.decode()
    start = body.index('<nav class="topnav"')
    bar = body[start : body.index("</nav>", start)]

    for label in ("Jõustumised", "Jõustuvad aktid", "Jõustumiste kalender", "Jälgimine"):
        assert label not in bar
    assert "/jalgimine/" not in bar
