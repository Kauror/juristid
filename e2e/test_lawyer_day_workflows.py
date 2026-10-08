"""Four lawyer days, each walked end to end in a real browser.

The rest of the browser suite holds individual controls; these four hold the
*journeys* — the cross-feature paths a lawyer actually walks — so a change that
leaves every control working but breaks the path between two of them fails
here. Written for the overnight/pre-release regression tier (owner's brief,
2026-10-08): few tests, long scenarios, a state assertion after every major
boundary.

1. A consultation from arrival to the second sent opinion and closure.
2. Current and planned work: promotion, editing, cancelling, explicit next.
3. Files and relations through Uus teema: display titles, renames, immutable
   original filenames, a relation ticked at creation.
4. Restricted visibility: the whole file stays invisible from every surface a
   READER or an ADMINISTRATOR can reach.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    ADMIN,
    DESKTOP_VIEWPORT,
    MARTIN,
    READER,
    SANDRA,
    chronology,
    close_through_stage,
    create_matter,
    document_overflows,
    finish_current_action,
    open_add_panel,
    open_done_form,
    sign_in,
    start_first_step,
    wait_for_htmx,
)
from e2e.legacy_opinions import _in_the_server, matter_id_of
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
PDF = b"%PDF-1.4 synthetic overnight journey"


def _et(days: int) -> str:
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def _upload(name: str) -> dict:
    return {"name": name, "mimeType": "application/pdf", "buffer": PDF}


def _zone(page):
    return page.locator("#praegune-tegevus")


def _planned_rows(page):
    return _zone(page).locator(".curact__plannedrow")


def _add_planned(page, text: str, days: int) -> None:
    open_add_panel(page, "lisa-planeeritud")
    page.locator("#lisa-planeeritud [name=text]").fill(text)
    page.locator("#lisa-planeeritud [name=target_date]").fill(_et(days))
    page.locator("#lisa-planeeritud button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def _register_opinion(page, *, file_name: str, answers_deadline: bool) -> None:
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(_upload(file_name))
    form.locator("[name=sent_on]").fill(_et(0))
    if answers_deadline:
        form.get_by_role("checkbox", name=re.compile("^Vastab arvamuse küsimisele")).check()
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/koja-arvamus/") and r.request.method == "POST"
    ):
        form.get_by_role("button", name="Registreeri arvamus").click()
    wait_for_htmx(page)


# ---------------------------------------------------------------------------
# Journey 1 — a consultation, from arrival to the second opinion and closure
# ---------------------------------------------------------------------------


def test_journey_consultation_to_repeat_opinion_and_closure(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)

    # Arrival: a titled file from the ministry, with the incoming letter and
    # the committee's first response deadline, on the consultation stage.
    title = unique_title("Teekond konsultatsioonist arvamuseni")
    incoming = tmp_path / "saabunud-kiri.pdf"
    incoming.write_bytes(PDF)
    page.goto(f"{base_url}/teemad/uus/")
    page.fill("#id_title", title)
    page.locator("#saatja-otsi").click()
    page.locator("#saatja-otsi").type(MINISTRY[:8], delay=20)
    page.locator("#saatja-tulemused").get_by_text(MINISTRY, exact=True).click()
    page.get_by_role("radio", name="Kooskõlastusringil", exact=True).check()
    page.fill("#id_response_deadline", _et(5))
    page.locator("#id_files").set_input_files([str(incoming)])
    expect(page.locator(".dropzone__file")).to_have_count(1)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    url = page.url

    header = page.locator("#teema-pais")
    expect(header).to_contain_text(f"Arvamuse tähtaeg {_et(5)}")

    # The day's plan: a current step and a queued one.
    start_first_step(page, text="Tutvu eelnõuga ja kaardista mõjutatud liikmed")
    expect(_zone(page)).to_contain_text("Tutvu eelnõuga")
    _add_planned(page, "Koosta liikmetele kokkuvõte kooskõlastusringi tulemustest", 7)
    expect(_planned_rows(page)).to_have_count(1)

    # The write-up members are pointed at.
    overview_url = "https://www.koda.ee/naidis/teekond-ulevaade"
    open_add_panel(page, "lisa-koduleht")
    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill(overview_url)
    panel.locator("[name=overview_title]").fill("Eelnõu ülevaade liikmetele")
    # Without the koda.ee fetch (off outside production) the kind is asked.
    panel.get_by_label("Ülevaade", exact=True).check()
    panel.get_by_role("button", name="Lisa ülevaade / uudis").click()
    page.wait_for_load_state("networkidle")
    row = chronology(page).locator(".uxtl__item", has_text="Eelnõu ülevaade liikmetele")
    expect(row).to_have_count(1)

    # The consultation round, pointing at that same overview.
    open_add_panel(page, "kaasamine-alusta")
    round_form = page.locator("#kaasamine-alusta")
    round_form.locator("[name=audience]").fill("Liikmed (e-kiri ja küsitlus)")
    round_form.locator("[name=feedback_deadline]").fill(_et(10))
    round_form.locator("[name=smaily_url]").fill("https://naidis.sendsmaily.net/teekond")
    round_form.locator("[name=alchemer_url]").fill("https://app.alchemer.eu/s3/teekond")
    round_form.locator("[name=website_url]").fill(overview_url)
    round_form.locator("input[type=file]").set_input_files(_upload("kaasamise-lisa.pdf"))
    round_form.locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    # The wait is owed, and the overview was reused rather than duplicated.
    waits = page.locator("#praegune-ootused")
    expect(waits).to_contain_text("Ootame tagasisidet")
    expect(waits.get_by_role("link", name="Smaily")).to_be_visible()
    expect(
        chronology(page).locator(".uxtl__item", has_text="Eelnõu ülevaade liikmetele")
    ).to_have_count(1)

    # The feedback arrives and ends the round.
    open_add_panel(page, "lisa-kaasamine")
    feedback = page.locator("#kaasamine-tagasiside")
    expect(feedback.locator("[name=engagement] option:checked")).to_contain_text("Liikmed")
    feedback.locator("[name=summary]").fill("Üksteist liiget vastas; mure halduskoormus.")
    feedback.locator("input[type=file]").first.set_input_files(_upload("tagasiside.pdf"))
    feedback.get_by_role("button", name="Salvesta tagasiside").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(0)

    # The first opinion goes out and answers the committee's deadline.
    _register_opinion(page, file_name="koja-arvamus-1.pdf", answers_deadline=True)
    header = page.locator("#teema-pais")
    expect(header).to_contain_text(re.compile("vastatud", re.IGNORECASE))

    # The committee asks again: a NEW deadline, which the old opinion must not
    # answer.
    open_add_panel(page, "marge-arvamuse-tahtaeg")
    page.locator("#id_response_deadline_date").fill(_et(30))
    page.locator("#marge-arvamuse-tahtaeg button[type=submit]").click()
    wait_for_htmx(page)
    header = page.locator("#teema-pais")
    expect(header).to_contain_text(f"Arvamuse tähtaeg {_et(30)}")
    expect(header.locator(".metaline__value--deadline")).not_to_contain_text(
        re.compile("vastatud", re.IGNORECASE)
    )

    # The second opinion answers the second request.
    _register_opinion(page, file_name="koja-arvamus-2.pdf", answers_deadline=True)
    expect(page.locator("#teema-pais")).to_contain_text(re.compile("vastatud", re.IGNORECASE))

    # The file moves on, then closes — and owes nothing afterwards.
    control = page.locator('#teema-pais details:has(select[aria-label="Hetkeseis"])')
    if not control.evaluate("el => el.open"):
        control.locator(".inlineedit__trigger").click()
    control.locator('select[aria-label="Hetkeseis"]').select_option(label="Riigikogus")
    control.get_by_role("button", name="Salvesta hetkeseisu muudatus").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teema-pais")).to_contain_text("Riigikogus")

    close_through_stage(
        page, stage="Rohkem ei tegele", title="Menetlus lõppes; teekond suleb teema"
    )
    expect(page.locator(".banner--closed")).to_be_visible()
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    expect(page.locator("#praegune-ootused [data-feedback-wait]")).to_have_count(0)

    # The two sent opinions both survive on the documents surface.
    page.goto(f"{url}dokumendid/")
    expect(page.locator("table.doctable tbody tr", has_text="koja-arvamus-1.pdf")).to_have_count(1)
    expect(page.locator("table.doctable tbody tr", has_text="koja-arvamus-2.pdf")).to_have_count(1)


# ---------------------------------------------------------------------------
# Journey 2 — current and planned work: promotion, edit, cancel, explicit next
# ---------------------------------------------------------------------------

LONG_PLAN = (
    "Valmista ette Koja täiendav arvamus juhuks, kui vahe-eesmärkide paindlikkuse "
    "muudatusettepanek teisel lugemisel läbi ei lähe, ja kaasa eelnevalt energeetika- "
    "ning tööstusettevõtete esindajad mõjuhinnangu arvude täpsustamiseks"
)


def test_journey_current_and_planned_work(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(
        page,
        base_url,
        unique_title("Teekond planeeritud tööga"),
        stage="Kooskõlastusringil",
        owner=MARTIN,
    )

    start_first_step(page, text="Loe eelnõu läbi ja märgi vaidluskohad")
    _add_planned(page, LONG_PLAN, 7)
    _add_planned(page, "Küsi liikmetelt seisukohta halduskoormuse kohta", 14)
    _add_planned(page, "Osale komisjoni istungil", 21)
    rows = _planned_rows(page)
    expect(rows).to_have_count(3)
    # Date-first grammar: the row leads with its date.
    first_row_text = rows.first.inner_text().strip()
    assert first_row_text.startswith(_et(7)), first_row_text

    # `Muuda` on a planned row changes that row and nothing else.
    target = rows.filter(has_text="Küsi liikmetelt seisukohta")
    target.locator(".curact__edit > summary").click()
    target.locator("[name=text]").fill("Küsi liikmetelt seisukohta ja koosta koondtabel")
    target.locator("[name=target_date]").fill(_et(15))
    target.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")
    expect(_planned_rows(page)).to_have_count(3)
    expect(_zone(page)).to_contain_text("koosta koondtabel")

    # `×` cancels one; the others stand.
    _planned_rows(page).filter(has_text="Osale komisjoni istungil").locator(
        ".curact__dismiss button"
    ).click()
    page.wait_for_load_state("networkidle")
    expect(_planned_rows(page)).to_have_count(2)

    # Completing with no named next step promotes the earliest planned row.
    finish_current_action(page, "Lugesin läbi; kaks vaidluskohta märgitud.")
    zone = _zone(page)
    expect(zone.locator(".curact__text")).to_contain_text("Valmista ette Koja täiendav")
    expect(_planned_rows(page)).to_have_count(1)
    # Exactly one current action.
    expect(zone.locator(".curact__text")).to_have_count(1)

    _add_planned(page, "Koosta kuu lõpu vahekokkuvõte juhile", 25)
    expect(_planned_rows(page)).to_have_count(2)

    # Completing WITH an explicit next step promotes nothing.
    open_done_form(page)
    page.fill("#id_praegune_body", "Täiendava arvamuse mustand valmis ja saadetud ringile.")
    page.fill("#id_praegune_jargmine_tekst", "Vii mustand juhatuse ette")
    page.fill("#id_praegune_jargmine_kuupaev", _et(4))
    page.locator("#praegune-tegevus-vorm button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    zone = _zone(page)
    expect(zone.locator(".curact__text")).to_contain_text("Vii mustand juhatuse ette")
    expect(_planned_rows(page)).to_have_count(2)

    # The history keeps what happened, in order.
    timeline = chronology(page)
    expect(timeline).to_contain_text("Lugesin läbi")
    expect(timeline).to_contain_text("Täiendava arvamuse mustand")

    # The work-heavy page holds at a narrow laptop and a phone width.
    for width in (1024, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        page.wait_for_load_state("networkidle")
        assert not document_overflows(page), f"sideways scroll at {width}px"
        expect(_zone(page).locator(".curact__text")).to_contain_text("Vii mustand juhatuse ette")
    page.set_viewport_size(DESKTOP_VIEWPORT)


# ---------------------------------------------------------------------------
# Journey 3 — files and relations through Uus teema
# ---------------------------------------------------------------------------


def test_journey_files_display_titles_and_relations(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)

    # An existing file the new one will be related to.
    anchor_word = "Lennundusseadustik"
    anchor_title = unique_title(f"{anchor_word} vana menetlus")
    create_matter(page, base_url, anchor_title, owner=SANDRA)

    # The new Teema: same distinctive word, so the draft offers the relation.
    page.goto(f"{base_url}/teemad/uus/")
    title = unique_title(f"{anchor_word} uus eelnõu")
    page.fill("#id_title", title)
    page.wait_for_timeout(800)
    page.wait_for_load_state("networkidle")
    card = page.locator(".relatedcard", has_text=anchor_title)
    expect(card.first).to_be_visible()
    card.first.locator("input[name=seo_teemaga]").check()

    # Two files, one display title changed before the save.
    first = tmp_path / "kaaskiri.pdf"
    second = tmp_path / "eelnou-tekst.pdf"
    first.write_bytes(PDF)
    second.write_bytes(PDF)
    page.locator("#id_files").set_input_files([str(first), str(second)])
    expect(page.locator(".dropzone__file")).to_have_count(2)
    # The title is text until its ✎ is pressed; Enter keeps what was typed.
    page.get_by_role("button", name="Muuda pealkirja: kaaskiri.pdf").click()
    page.get_by_role("textbox", name="Pealkiri: kaaskiri.pdf").fill("Kaaskiri ministeeriumilt")
    page.get_by_role("textbox", name="Pealkiri: kaaskiri.pdf").press("Enter")
    expect(
        page.locator(".dropzone__file .titleedit__text", has_text="Kaaskiri ministeeriumilt")
    ).to_have_count(1)

    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    url = page.url

    # The relation ticked at creation is on the file («Seotud materjalid»).
    expect(
        page.locator("#seotud-materjalid [data-related-matter]", has_text=anchor_word)
    ).to_have_count(1)

    # Documents: the typed display title shows, the original filename stays.
    page.goto(f"{url}dokumendid/")
    titled = page.locator("table.doctable tbody tr", has_text="Kaaskiri ministeeriumilt")
    expect(titled).to_have_count(1)
    expect(titled.locator(".doctable__filename")).to_contain_text("kaaskiri.pdf")
    plain = page.locator("table.doctable tbody tr", has_text="eelnou-tekst.pdf")
    expect(plain).to_have_count(1)

    # The download still carries the immutable original filename.
    with page.expect_download() as arrival:
        page.get_by_role("link", name="Tõmba alla Kaaskiri ministeeriumilt").click()
    assert arrival.value.suggested_filename == "kaaskiri.pdf"

    # A later rename from `⋯` changes the title and nothing else.
    menu = plain.locator("details.docmenu")
    menu.locator("summary.opinionmenu__trigger").click()
    menu.locator("input[name=title]").fill("Eelnõu terviktekst")
    menu.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")
    renamed = page.locator("table.doctable tbody tr", has_text="Eelnõu terviktekst")
    expect(renamed).to_have_count(1)
    expect(renamed.locator(".doctable__filename")).to_contain_text("eelnou-tekst.pdf")

    # The relation reads from the other side too.
    page.goto(f"{base_url}/teemad/?olek=koik&q={anchor_word}")
    page.get_by_role("link", name=re.compile(re.escape(anchor_title))).first.click()
    page.wait_for_load_state("networkidle")
    expect(
        page.locator("#seotud-materjalid [data-related-matter]", has_text=anchor_word)
    ).to_have_count(1)


# ---------------------------------------------------------------------------
# Journey 4 — restricted visibility, checked from the outside in
# ---------------------------------------------------------------------------


def test_journey_restricted_file_is_invisible_from_every_surface(page, base_url):
    sign_in(page, base_url, SANDRA)

    secret = unique_title("Konfidentsiaalne maksumenetlus")
    url = create_matter(page, base_url, secret, sender=MINISTRY, owner=SANDRA)

    # Real work on the file: a step, a completion with evidence, a next step.
    start_first_step(page, text="Kogu ettevõtjate konfidentsiaalne tagasiside")
    open_done_form(page)
    page.fill("#id_praegune_body", "Kolm ettevõtet kirjeldasid mõju; materjal tundlik.")
    page.locator("#praegune-tegevus-vorm input[type=file]").set_input_files(
        _upload("konfidentsiaalne-lisa.pdf")
    )
    page.fill("#id_praegune_jargmine_tekst", "Lepi kokku anonüümitud kokkuvõte")
    page.locator("#praegune-tegevus-vorm button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    expect(_zone(page)).to_contain_text("anonüümitud kokkuvõte")

    # Restricted the way an administrator restricts one: through the canonical
    # service in the server's own process (docs/adr/0096 §3).
    _in_the_server(
        "import os;"
        "from django.contrib.auth import get_user_model;"
        "from app.core.enums import Visibility;"
        "from app.matters.models import Matter;"
        "from app.matters.services import set_matter_visibility;"
        "m = Matter.objects.get(pk=os.environ['E2E_LEGACY_MATTER']);"
        "u = get_user_model().objects.get(upn=os.environ['E2E_LEGACY_ACTOR']);"
        "set_matter_visibility(matter=m, visibility=Visibility.RESTRICTED, actor=u);"
        "print(m.pk)",
        {"E2E_LEGACY_MATTER": matter_id_of(url), "E2E_LEGACY_ACTOR": SANDRA.upn},
    )

    # The owner still reads all of it.
    page.goto(url)
    expect(page.locator("#teema-pais")).to_contain_text(secret.split(" ")[0])
    expect(_zone(page)).to_contain_text("anonüümitud kokkuvõte")

    # The READER and the ADMINISTRATOR each find nothing, anywhere. Signing in
    # again replaces the session, so no sign-out is needed between personas —
    # and the last page visited is a 404 with no nav to sign out from anyway.
    for outsider in (READER, ADMIN):
        sign_in(page, base_url, outsider)

        page.goto(f"{base_url}/minu-asjad/")
        expect(page.get_by_text(secret)).to_have_count(0)

        page.goto(f"{base_url}/ulevaade/")
        expect(page.get_by_text(secret)).to_have_count(0)

        page.goto(f"{base_url}/teemad/?olek=koik")
        expect(page.get_by_role("link", name=secret)).to_have_count(0)

        page.goto(f"{base_url}/teemad/?olek=koik&q={secret.split(' ')[1]}")
        expect(page.locator("body")).not_to_contain_text(secret)

        search = page.get_by_placeholder("Otsi teemat, viidet, asutust…")
        if search.count():
            search.fill(secret.split(" ")[0])
            search.press("Enter")
            page.wait_for_load_state("networkidle")
            expect(page.locator("body")).not_to_contain_text(secret)

        assert page.goto(url).status == 404
        assert page.goto(f"{url}dokumendid/").status == 404
