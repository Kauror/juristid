"""The register watch: sources read, findings proposed, a lawyer adds them (ADR 0217).

No test contacts a register or the model: the sources' one door (`get_json`)
and the model client are replaced here.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.models import MatterProceduralDevelopment
from app.register_watch import ai, watch
from app.register_watch.models import (
    FindingStatus,
    Relevance,
    WatchFinding,
    WatchRun,
    WatchSource,
    WatchTarget,
)
from app.register_watch.sources import adr, http, pdr, riigikogu
from app.register_watch.sources.http import FetchError
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 17)
UUID = "0aaca896-3deb-49fc-83bc-2902d4bd441d"

BILL_LIST = {
    "_embedded": {
        "content": [
            {
                "uuid": "old-973",
                "mark": 973,
                "draftTypeCode": "SE",
                "membership": 10,
                "initiated": "2006-09-13",
            },
            {
                "uuid": UUID,
                "mark": 973,
                "draftTypeCode": "SE",
                "membership": 15,
                "initiated": "2026-06-18",
            },
        ]
    }
}
BILL = {
    "uuid": UUID,
    "readings": [
        {
            "readingCode": "INITIATION",
            "proceedingEvents": [{"date": "2026-06-18T10:21:55", "status": "ALGATATUD"}],
        },
        {
            "readingCode": "FIRST_READING",
            "proceedingEvents": [
                {
                    "date": "2026-09-14T11:10:00",
                    "sittingTitle": "Komisjoni istung esmaspäev, 14.09.2026",
                    "committeeDecisions": [
                        {"decision": {"value": "Teha ettepanek esimene lugemine lõpetada"}}
                    ],
                },
                {
                    "date": "2026-10-13T14:00:00",
                    "sittingTitle": "Komisjoni istung teisipäev",
                    "committeeDecisions": [],
                },
            ],
        },
    ],
    "texts": [
        {
            "readingCode": "FIRST_READING",
            "document": {"uuid": "t1", "title": "973 SE I"},
            "file": {"created": "2026-09-21T10:00:00"},
        },
        {
            "readingCode": "FIRST_READING",
            "document": {"uuid": "t1", "title": "973 SE I"},
            "file": {"created": "2026-09-22T10:00:00"},
        },
    ],
    "opinions": [
        {
            "uuid": "o1",
            "title": "HTM-i arvamus 973 SE kohta",
            "date": "2026-11-06",
            "reference": "1-1/15-973/16",
        },
    ],
}
HTM_SEARCH = {
    "recordsFiltered": 2,
    "data": [
        {
            "id": "h1",
            "regCode": "8-1/26/5965",
            "regDate": "2026-11-06T00:00:00",
            "title": "Muudatusettepanekud (973 SE) eelnõule",
            "extOrganization": "Riigikogu kultuurikomisjon",
        },
        {
            "id": "h2",
            "regCode": "1-15/26/47",
            "regDate": "2026-08-06T00:00:00",
            "title": "Eelnõu (973 SE) menetlus Riigikogus",
            "extOrganization": "",
        },
    ],
}


@pytest.fixture
def registers(monkeypatch):
    """Answer every source call from the fixtures above; record the calls."""
    calls: list[tuple[str, str, dict]] = []
    answers = {
        ("api.riigikogu.ee", "/api/volumes/drafts"): BILL_LIST,
        ("api.riigikogu.ee", f"/api/volumes/drafts/{UUID}"): BILL,
        ("dok.hm.ee", "/api/v1/documents.json"): HTM_SEARCH,
        ("adr.envir.ee", "/api/v1/documents.json"): {"recordsFiltered": 0, "data": []},
    }

    def fake(host, path, params=None):
        calls.append((host, path, dict(params or {})))
        if (host, path) not in answers:
            raise FetchError(f"{host} vastas 404")
        return answers[(host, path)]

    monkeypatch.setattr(riigikogu, "get_json", fake)
    monkeypatch.setattr(pdr, "get_json", fake)
    pages: dict[str, str] = {}

    def fake_form(host, path, fields):
        calls.append((host, path, dict(fields)))
        return pages.get(path, ADR_EMPTY)

    monkeypatch.setattr(adr, "get_text", lambda host, path: "")
    monkeypatch.setattr(adr, "post_form", fake_form)
    return SimpleNamespace(calls=calls, answers=answers, pages=pages)


ADR_EMPTY = "<table><tr><th>Viit</th></tr></table> Näitan: 0-0 Kokku: 0"
ADR_PAGE = (
    "<table><tr><th>Viit</th><th>Reg. kpv</th><th>Pealkiri</th></tr>"
    '<tr><td>1.1-10.1/1114-11</td><td>15.11.2026</td><td><a href="/ram/dokument/19160596">'
    "Ettepanekud kindlustustegevuse seaduse muutmise seaduse eelnõule</a></td>"
    "<td>Õigusakti eelnõu</td><td>Eesti Kindlustusseltside Liit</td></tr>"
    '<tr><td>1.1-10.1/1114-9</td><td>08.07.2026</td><td><a href="/ram/dokument/18857238">'
    "Kindlustustegevuse seaduse eelnõu</a></td><td>Sissetulev kiri</td><td></td></tr>"
    "</table> Näitan: 1-2 Kokku: 2"
)


def _bill_teema(**extra):
    return factories.MatterFactory(title="Kutse- ja oskusseaduse eelnõu (973 SE)", **extra)


# -- sources -----------------------------------------------------------------------


def test_the_bill_number_is_read_from_the_title():
    assert riigikogu.bill_mark("Äriseadustiku … eelnõu (1008 SE)") == (1008, "SE")
    assert riigikogu.bill_mark("EL kliimakindluse raamistik") is None


def test_the_bill_is_the_current_memberships(registers):
    assert riigikogu.find_bill(973, "SE")["uuid"] == UUID


def test_a_bill_reads_as_steps_texts_and_opinions(registers):
    items = {item.external_id: item for item in riigikogu.bill_items(UUID)}

    assert items["event:INITIATION:2026-06-18T10:21:55"].title == "Eelnõu algatati"
    assert items["event:FIRST_READING:2026-09-14T11:10:00"].title.startswith("Juhtivkomisjon: Teha")
    assert items["event:FIRST_READING:2026-10-13T14:00:00"].title == "Juhtivkomisjon arutas eelnõu"
    assert items["text:t1"].occurred_on == dt.date(2026, 9, 21)
    assert items["opinion:o1"].detail == "1-1/15-973/16"
    assert all(
        item.url == f"https://www.riigikogu.ee/tegevus/eelnoud/{UUID}/" for item in items.values()
    )


def test_a_register_search_reads_numbers_senders_and_links(registers):
    first = pdr.search(pdr.HTM, "973 SE")[0]

    assert first.detail == "8-1/26/5965 · Riigikogu kultuurikomisjon"
    assert first.url == "https://dok.hm.ee/et/document.html?id=h1"
    assert first.occurred_on == dt.date(2026, 11, 6)


def test_the_door_is_shut_in_tests_and_fenced_to_three_hosts():
    with pytest.raises(FetchError, match="välja lülitatud"):
        http.get_json("api.riigikogu.ee", "/api/volumes/drafts")
    with override_settings(REGISTER_WATCH_FETCH=True), pytest.raises(FetchError, match="keelatud"):
        http.get_json("eelnoud.valitsus.ee", "/")


# -- the run -----------------------------------------------------------------------


def test_a_bill_teema_is_watched_in_three_places(registers):
    matter = _bill_teema()
    unrelated = factories.MatterFactory(title="EL kliimakindluse raamistik")

    record = watch.run([matter, unrelated], use_ai=False, today=TODAY)

    targets = {t.source: t for t in WatchTarget.objects.filter(matter=matter)}
    assert set(targets) == {
        WatchSource.RIIGIKOGU,
        WatchSource.HTM_REGISTER,
        WatchSource.KLIM_REGISTER,
        WatchSource.ADR_JDM,
        WatchSource.ADR_MKM,
        WatchSource.ADR_RAM,
        WatchSource.ADR_SOM,
        WatchSource.ADR_KUM,
        WatchSource.ADR_KAM,
    }
    assert targets[WatchSource.ADR_JDM].identifier == "973 SE"
    assert targets[WatchSource.RIIGIKOGU].identifier == UUID
    assert targets[WatchSource.HTM_REGISTER].identifier == "973 SE"
    assert not WatchTarget.objects.filter(matter=unrelated).exists()
    assert (record.targets_checked, record.errors) == (9, [])


def test_the_first_check_offers_only_what_is_recent(registers):
    matter = _bill_teema()

    record = watch.run([matter], use_ai=False, today=TODAY)

    offered = set(
        WatchFinding.objects.filter(matter=matter, status=FindingStatus.PENDING).values_list(
            "title", flat=True
        )
    )
    assert offered == {
        "Juhtivkomisjon arutas eelnõu",
        "HTM-i arvamus 973 SE kohta",
        "Muudatusettepanekud (973 SE) eelnõule",
    }
    assert WatchFinding.objects.filter(matter=matter, status=FindingStatus.BASELINE).count() == 4
    assert (record.findings_new, record.findings_offered) == (7, 3)


def test_a_later_check_offers_everything_new_and_nothing_twice(registers):
    matter = _bill_teema()
    watch.run([matter], use_ai=False, today=TODAY)
    registers.answers[("api.riigikogu.ee", f"/api/volumes/drafts/{UUID}")] = {
        **BILL,
        "opinions": [
            *BILL["opinions"],
            {"uuid": "o2", "title": "Uus arvamus", "date": "2026-01-02"},
        ],
    }

    record = watch.run([matter], use_ai=False, today=TODAY + dt.timedelta(days=1))

    assert (record.findings_new, record.findings_offered) == (1, 1)
    assert WatchFinding.objects.get(external_id="opinion:o2").status == FindingStatus.PENDING


def test_a_failing_register_is_recorded_and_the_rest_goes_on(registers):
    matter = _bill_teema()
    del registers.answers[("dok.hm.ee", "/api/v1/documents.json")]

    record = watch.run([matter], use_ai=False, today=TODAY)

    assert record.targets_checked == 8
    assert record.errors[0]["viga"] == "dok.hm.ee vastas 404"
    assert WatchTarget.objects.get(matter=matter, source=WatchSource.HTM_REGISTER).last_error


def test_the_command_has_a_dry_run(registers, capsys):
    _bill_teema(reference_year=2026, reference_number=190)

    call_command("watch_registers", "--teema", "2026_190", "--dry-run")

    assert "DRY-RUN checked=9" in capsys.readouterr().out
    assert not WatchFinding.objects.exists() and not WatchRun.objects.exists()


# -- the model ---------------------------------------------------------------------


class FakeClient:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.sent: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.sent.append(request)
        prompt = request["messages"][0]["content"]
        ids = [doc["id"] for doc in json.loads(prompt.split("Documents found:\n", 1)[1])]
        items = [
            {"id": i, "verdict": v, "note": f"märkus {n}"}
            for n, (i, v) in enumerate(zip(ids, self.verdicts, strict=False))
        ]
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="text", text=json.dumps({"items": items}))],
        )


@override_settings(REGISTER_WATCH_AI=True)
def test_the_model_judges_only_what_a_title_search_found(registers):
    matter = _bill_teema()
    client = FakeClient(["RELEVANT", "UNRELATED"])

    record = watch.run([matter], use_ai=True, ai_client=client, today=TODAY)

    assert len(client.sent) == 1
    sent = client.sent[0]["messages"][0]["content"]
    assert (
        "Muudatusettepanekud" in sent and "HTM-i arvamus" not in sent
    )  # Riigikogu items stay home
    judged = WatchFinding.objects.get(external_id="doc:h1")
    assert (judged.relevance, judged.ai_note) == (Relevance.RELEVANT, "märkus 0")
    assert (record.ai_calls, record.ai_input_tokens) == (1, 100)
    assert client.sent[0]["output_config"]["format"]["type"] == "json_schema"


@override_settings(REGISTER_WATCH_AI=True)
def test_a_restricted_teema_is_never_sent(registers):
    matter = _bill_teema(visibility=Visibility.RESTRICTED)
    client = FakeClient(["RELEVANT"])

    watch.run([matter], use_ai=True, ai_client=client, today=TODAY)

    assert client.sent == []
    assert WatchFinding.objects.get(external_id="doc:h1").relevance == Relevance.UNJUDGED


def test_the_model_is_off_unless_switched_on(registers):
    client = FakeClient(["RELEVANT"])

    watch.run([_bill_teema()], use_ai=True, ai_client=client, today=TODAY)

    assert client.sent == []
    assert ai.enabled() is False


# -- the lawyer --------------------------------------------------------------------


def _finding(matter, **extra):
    target = WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.HTM_REGISTER,
        identifier="973 SE",
        label="973 SE",
        origin="TITLE",
    )
    return WatchFinding.objects.create(
        target=target,
        matter=matter,
        external_id=extra.pop("external_id", "doc:h1"),
        title=extra.pop("title", "Muudatusettepanekud (973 SE) eelnõule"),
        occurred_on=dt.date(2026, 11, 6),
        url="https://dok.hm.ee/et/document.html?id=h1",
        detail="8-1/26/5965 · Riigikogu kultuurikomisjon",
        **extra,
    )


def test_the_page_lists_my_waiting_documents(client, specialist):
    mine = _finding(_bill_teema(owner=specialist))
    other = _finding(
        factories.MatterFactory(title="Teise teema"), title="Teise dokument", external_id="doc:x"
    )
    client.force_login(specialist)

    page = client.get(reverse("register_watch:review"))
    everyone = client.get(reverse("register_watch:review"), {"kelle": "koik"})

    body = page.content.decode()
    assert mine.title in body and other.title not in body
    assert "Lisa märkena" in body
    assert other.title in everyone.content.decode()


def test_a_reader_sees_the_documents_without_the_buttons(client, reader):
    _finding(_bill_teema())
    client.force_login(reader)

    body = client.get(reverse("register_watch:review"), {"kelle": "koik"}).content.decode()

    assert "Muudatusettepanekud" in body and "Lisa märkena" not in body


def test_adding_records_a_menetluse_areng_on_the_teema(client, specialist):
    matter = _bill_teema(owner=specialist)
    finding = _finding(matter)
    client.force_login(specialist)

    response = client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}), {"otsus": "lisa"}
    )

    assert response.status_code == 302
    development = MatterProceduralDevelopment.objects.get(matter=matter)
    assert development.title == finding.title
    assert development.occurred_on == dt.date(2026, 11, 6)
    assert "dok.hm.ee/et/document.html?id=h1" in development.note
    finding.refresh_from_db()
    assert (finding.status, finding.development) == (FindingStatus.ADDED, development)


def test_skipping_writes_nothing_on_the_teema(client, specialist):
    matter = _bill_teema(owner=specialist)
    finding = _finding(matter)
    client.force_login(specialist)

    client.post(reverse("register_watch:decide", kwargs={"pk": finding.pk}), {"otsus": "jata"})

    finding.refresh_from_db()
    assert finding.status == FindingStatus.DISMISSED
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()


def test_a_reader_cannot_add(client, reader):
    finding = _finding(_bill_teema())
    client.force_login(reader)

    response = client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}), {"otsus": "lisa"}
    )

    assert response.status_code == 404
    finding.refresh_from_db()
    assert finding.status == FindingStatus.PENDING


def test_the_teema_page_links_to_its_waiting_documents(client, specialist):
    matter = _bill_teema(owner=specialist, reference_year=2026, reference_number=190)
    _finding(matter)
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Registrites 1 uus dokument" in body
    assert f"?teema={matter.pk}" in body


def test_the_teema_page_leaves_out_what_the_model_called_unrelated(client, specialist):
    matter = _bill_teema(owner=specialist)
    _finding(matter, relevance=Relevance.UNRELATED)
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Registrites" not in body


# -- the bell ----------------------------------------------------------------------


def test_a_run_tells_the_owner_once_and_adds_to_the_unread_notice(registers, specialist):
    from app.notifications import inbox
    from app.register_watch.models import RegisterNotice

    matter = _bill_teema(owner=specialist)
    watch.run([matter], use_ai=False, today=TODAY)
    registers.answers[("api.riigikogu.ee", f"/api/volumes/drafts/{UUID}")] = {
        **BILL,
        "opinions": [
            *BILL["opinions"],
            {"uuid": "o2", "title": "Uus arvamus", "date": "2026-11-17"},
        ],
    }
    watch.run([matter], use_ai=False, today=TODAY)

    notice = RegisterNotice.objects.get(recipient=specialist, matter=matter)
    assert notice.documents == 4
    items = [
        item for item in inbox.inbox_for(specialist, TODAY) if item.type == inbox.TYPE_REGISTER
    ]
    assert [(item.label, item.note) for item in items] == [
        ("Registris uus dokument", "4 uut dokumenti")
    ]
    assert items[0].url.endswith(f"?teema={matter.display_reference}")
    assert inbox.inbox_count(specialist, TODAY) >= 1


def test_no_notice_without_an_owner_or_for_what_the_model_called_unrelated(registers, specialist):
    from app.register_watch.models import RegisterNotice

    nobodys = _bill_teema(owner=None)
    watch.run([nobodys], use_ai=False, today=TODAY)
    assert not RegisterNotice.objects.exists()

    with override_settings(REGISTER_WATCH_AI=True):
        mine = factories.MatterFactory(title="Teine eelnõu (973 SE)", owner=specialist)
        registers.answers[("api.riigikogu.ee", "/api/volumes/drafts")] = {
            "_embedded": {"content": []}
        }
        watch.run([mine], use_ai=True, ai_client=FakeClient(["UNRELATED"]), today=TODAY)
    assert not RegisterNotice.objects.filter(matter=mine).exists()


def test_reading_the_notice_and_dealing_with_the_documents_takes_it_out(client, specialist):
    from app.notifications import inbox
    from app.register_watch import notices

    matter = _bill_teema(owner=specialist)
    finding = _finding(matter)
    row = notices.notify(matter, 1)
    client.force_login(specialist)

    client.post(reverse("notifications:register_read", kwargs={"pk": row.pk}))
    row.refresh_from_db()
    assert row.read_at is not None
    client.post(reverse("notifications:register_unread", kwargs={"pk": row.pk}))
    assert notices.due_unread(specialist).count() == 1

    watch.dismiss(finding, actor=specialist)

    assert notices.due_unread(specialist).count() == 0
    assert not [i for i in inbox.inbox_for(specialist, TODAY) if i.type == inbox.TYPE_REGISTER]


def test_a_colleague_cannot_read_my_notice(client, specialist):
    from app.register_watch import notices

    matter = _bill_teema(owner=specialist)
    _finding(matter)
    row = notices.notify(matter, 1)
    colleague = factories.UserFactory(role=specialist.role)
    client.force_login(colleague)

    response = client.post(reverse("notifications:register_read", kwargs={"pk": row.pk}))

    assert response.status_code == 404


def test_mark_all_read_includes_the_register_notice(specialist):
    from django.utils import timezone

    from app.notifications import acknowledgement
    from app.register_watch import notices

    matter = _bill_teema(owner=specialist)
    _finding(matter)
    notices.notify(matter, 1)

    acknowledgement.mark_all_read(specialist, until=timezone.now())

    assert notices.due_unread(specialist).count() == 0


# -- «Lisa link» -------------------------------------------------------------------


def test_add_link_keeps_the_address_as_a_menetluse_link_and_nothing_else(client, specialist):
    from app.matters.enums import ProceduralLinkKind
    from app.matters.models import MatterProceduralLink

    matter = _bill_teema(owner=specialist)
    finding = _finding(matter)
    client.force_login(specialist)

    client.post(reverse("register_watch:decide", kwargs={"pk": finding.pk}), {"otsus": "link"})

    link = MatterProceduralLink.objects.get(matter=matter)
    assert (link.kind, link.url, link.label) == (
        ProceduralLinkKind.MINISTRY_REGISTER,
        "https://dok.hm.ee/et/document.html?id=h1",
        "8-1/26/5965",
    )
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    finding.refresh_from_db()
    assert finding.status == FindingStatus.LINKED


# -- ministry registers by words (ADR 0217 §7) ---------------------------------------


def test_the_search_words_are_the_titles_distinctive_words():
    from app.register_watch import phrases

    assert phrases.words("Pankrotiseaduse ja kohtutäituri seaduse muutmise seaduse eelnõu VTK") == (
        "Pankrotiseaduse kohtutäituri"
    )
    assert (
        phrases.words("Tulumaksuseaduse muutmise seaduse eelnõu (maksuküür)") == "Tulumaksuseaduse"
    )
    assert phrases.words("Seaduse eelnõu") == ""


def test_an_adr_page_reads_as_documents_and_a_strange_page_is_an_error():
    items = adr.parse(adr.RAM, ADR_PAGE)

    assert [i.external_id for i in items] == ["adr:ram:19160596", "adr:ram:18857238"]
    assert items[0].occurred_on == dt.date(2026, 11, 15)
    assert items[0].url == "https://adr.rik.ee/ram/dokument/19160596"
    assert items[0].detail == "1.1-10.1/1114-11 · Õigusakti eelnõu · Eesti Kindlustusseltside Liit"
    with pytest.raises(FetchError, match="tulemuste leht"):
        adr.parse(adr.RAM, "<html>Vabandust</html>")


def _ministry_teema(title, ministry, **extra):
    from app.organisations.models import OrganisationType
    from app.reporting.models import ClassificationSuggestion

    matter = factories.MatterFactory(title=title, **extra)
    org = factories.OrganisationFactory(name=ministry, organisation_type=OrganisationType.MINISTRY)
    ClassificationSuggestion.objects.create(matter=matter, sender=org, confidence="h", source="t")
    return matter


def test_a_ministry_teema_is_searched_in_its_register_by_its_words(registers, specialist):
    matter = _ministry_teema(
        "Kindlustustegevuse seaduse muutmise seaduse eelnõu",
        "Rahandusministeerium",
        owner=specialist,
    )
    registers.pages["/ram/otsing"] = ADR_PAGE

    record = watch.run([matter], use_ai=False, today=TODAY)

    target = WatchTarget.objects.get(matter=matter)
    assert (target.source, target.identifier, target.origin) == (
        WatchSource.ADR_RAM,
        "Kindlustustegevuse",
        "PHRASE",
    )
    posted = [fields for host, path, fields in registers.calls if path == "/ram/otsing"]
    assert posted == [
        {"title": "Kindlustustegevuse", "regDateBegin": "20.07.2026", "pageNumber": "1"}
    ]
    assert record.findings_offered == 1  # 15.11; 08.07 is older than the first check's 45 days
    assert WatchFinding.objects.get(status=FindingStatus.PENDING).title.startswith("Ettepanekud")


def test_a_lawyers_words_replace_the_titles_and_survive_the_next_run(client, registers, specialist):
    matter = _ministry_teema(
        "Kindlustustegevuse seaduse muutmise seaduse eelnõu",
        "Rahandusministeerium",
        owner=specialist,
        reference_year=2026,
        reference_number=73,
    )
    watch.run([matter], use_ai=False, today=TODAY)
    client.force_login(specialist)

    response = client.post(
        reverse("register_watch:phrase", kwargs={"pk": matter.pk}),
        {"allikas": "ADR_RAM", "sonad": "  kindlustustegevuse   võlaõigusseaduse "},
    )
    watch.run([matter], use_ai=False, today=TODAY)

    assert response.status_code == 302 and response["Location"].endswith("?teema=2026_73")
    active = WatchTarget.objects.get(matter=matter, active=True)
    assert (active.identifier, active.origin) == ("kindlustustegevuse võlaõigusseaduse", "PERSON")
    assert WatchTarget.objects.filter(matter=matter, origin="PHRASE", active=False).count() == 1


def test_empty_words_switch_the_search_off_for_good(registers, specialist):
    matter = _ministry_teema("Tulumaksuseaduse muutmise seaduse eelnõu", "Rahandusministeerium")

    watch.set_phrase(matter, source="ADR_RAM", text="")
    watch.run([matter], use_ai=False, today=TODAY)

    assert not WatchTarget.objects.filter(matter=matter, active=True).exists()


def test_a_reader_cannot_change_the_words(client, reader):
    matter = _ministry_teema("Tulumaksuseaduse muutmise seaduse eelnõu", "Rahandusministeerium")
    client.force_login(reader)

    response = client.post(
        reverse("register_watch:phrase", kwargs={"pk": matter.pk}),
        {"allikas": "ADR_RAM", "sonad": "x"},
    )

    assert response.status_code == 404
    assert not WatchTarget.objects.filter(matter=matter).exists()


def test_a_pdr_phrase_keeps_only_titles_with_every_word(registers):
    matter = _ministry_teema("Kutse- ja oskusseaduse eelnõu", "Haridus- ja Teadusministeerium")
    registers.answers[("dok.hm.ee", "/api/v1/documents.json")] = {
        "data": [
            {
                "id": "a",
                "regDate": "2026-11-06T00:00:00",
                "title": "Kutse- ja oskusseaduse muudatused",
            },
            {"id": "b", "regDate": "2026-11-06T00:00:00", "title": "Kutse- ja eriharidus aruanne"},
        ]
    }

    watch.run([matter], use_ai=False, today=TODAY)

    assert list(WatchFinding.objects.values_list("external_id", flat=True)) == ["doc:a"]


def test_every_open_teema_has_the_way_to_its_watch(client, specialist):
    matter = factories.MatterFactory(owner=specialist, reference_year=2026, reference_number=12)
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    review = client.get(reverse("register_watch:review"), {"teema": "2026_12"}).content.decode()

    assert "Registrite jälgimine" in body
    assert "Otsingusõnad" in review and "Ei jälgita." in review


# -- Haldus → Registrite jälgimine (ADR 0217 §8) -----------------------------------


class FailingClient:
    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        error = RuntimeError("sk-ant-secret must never be shown")
        error.status_code = 401  # type: ignore[attr-defined]
        raise error


@override_settings(REGISTER_WATCH_AI=True, REGISTER_WATCH_AI_MODEL="claude-opus-5-5")
def test_a_run_records_the_model_what_it_judged_and_its_failures(registers):
    judged = watch.run(
        [_bill_teema()], use_ai=True, ai_client=FakeClient(["RELEVANT"]), today=TODAY
    )
    failed = watch.run(
        [_bill_teema()],
        use_ai=True,
        ai_client=FailingClient(),
        today=TODAY,
    )

    assert (judged.ai_model, judged.ai_judged, judged.ai_failures) == ("claude-opus-5-5", 1, 0)
    assert (failed.ai_calls, failed.ai_failures) == (0, 1)
    assert failed.errors[-1] == {
        "allikas": "Claude",
        "viga": "1 päringut ebaõnnestus: RuntimeError 401",
    }


def test_a_run_without_the_model_names_none(registers):
    assert watch.run([_bill_teema()], use_ai=True, today=TODAY).ai_model == ""


def test_haldus_lists_the_runs_their_documents_and_the_sources(client, specialist, registers):
    record = watch.run([_bill_teema()], use_ai=False, today=TODAY)
    client.force_login(specialist)

    runs = client.get(reverse("haldus:register_watch")).content.decode()
    one = client.get(reverse("haldus:register_watch_run", args=[record.pk])).content.decode()
    sources = client.get(reverse("haldus:register_watch_sources")).content.decode()

    assert reverse("haldus:register_watch_run", args=[record.pk]) in runs
    assert "Väljas" in runs  # the model was off for the run
    assert "Muudatusettepanekud" in one
    assert "973 SE" in sources


def test_haldus_shows_the_section_to_business_readers_only(client, reader, administrator):
    client.force_login(reader)
    assert client.get(reverse("haldus:register_watch")).status_code == 200
    assert "Registrite jälgimine" in client.get(reverse("haldus:index")).content.decode()

    client.force_login(administrator)
    assert client.get(reverse("haldus:register_watch")).status_code == 404
    assert client.get(reverse("haldus:register_watch_sources")).status_code == 404


# -- a night that goes wrong (audit QA26-BUG-04, OPS-01, BUG-05, DUP-04, TEST-06) ------


def _bill_with_null_decision():
    reading = {
        "readingCode": "SECOND_READING",
        "proceedingEvents": [
            {
                "date": "2026-11-10T10:00:00",
                "sittingTitle": "Komisjoni istung",
                "committeeDecisions": [{"decision": {"value": None}}],
            }
        ],
    }
    return {**BILL, "readings": [*BILL["readings"], reading]}


def test_a_null_committee_decision_is_read_as_no_decision(registers):
    registers.answers[("api.riigikogu.ee", f"/api/volumes/drafts/{UUID}")] = (
        _bill_with_null_decision()
    )

    record = watch.run([_bill_teema()], use_ai=False, today=TODAY)

    assert record.errors == []
    assert record.targets_checked == 9


def test_an_unexpected_answer_is_one_sources_error_and_the_night_goes_on(registers, monkeypatch):
    first, second = _bill_teema(), _bill_teema()
    real = riigikogu.bill_items
    calls = {"n": 0}

    def once_broken(identifier):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AttributeError("'NoneType' object has no attribute 'strip'")
        return real(identifier)

    monkeypatch.setattr(riigikogu, "bill_items", once_broken)

    record = watch.run([first, second], use_ai=False, today=TODAY)

    assert record.finished_at is not None and not record.aborted
    assert [e["teema"] for e in record.errors] == [first.display_reference]
    assert record.errors[0]["viga"].startswith("AttributeError")
    assert record.targets_checked == 9 + 8
    assert all(t.checked_at for t in WatchTarget.objects.filter(matter=second))


def test_a_teema_whose_sources_cannot_be_set_up_does_not_stop_the_others(registers, monkeypatch):
    first, second = _bill_teema(), _bill_teema()
    real = watch.ensure_targets

    def broken_for_first(matter):
        if matter.pk == first.pk:
            raise KeyError("uuid")
        return real(matter)

    monkeypatch.setattr(watch, "ensure_targets", broken_for_first)

    record = watch.run([first, second], use_ai=False, today=TODAY)

    assert record.errors == [{"teema": first.display_reference, "viga": "KeyError: 'uuid'"}]
    assert record.targets_checked == 9


def test_a_run_that_stops_says_so_and_turns_the_status_red(registers):
    def teemad():
        yield _bill_teema()
        raise RuntimeError("andmebaas kadus")

    with pytest.raises(RuntimeError):
        watch.run(teemad(), use_ai=False, today=TODAY)

    record = WatchRun.objects.get()
    assert record.finished_at is not None and record.aborted
    assert record.errors[-1] == {
        "viga": "Kontroll katkes: RuntimeError: andmebaas kadus",
        "katkes": True,
    }
    with override_settings(REGISTER_WATCH_FETCH=True):
        assert watch.watch_health() == watch.Health(
            False, "last run stopped before its end", "Viimane kontroll katkes."
        )


def _ran(*, started_hours_ago, finished=True, **fields):
    from django.utils import timezone

    started = timezone.now() - dt.timedelta(hours=started_hours_ago)
    return WatchRun.objects.create(
        started_at=started,
        finished_at=started + dt.timedelta(minutes=16) if finished else None,
        **fields,
    )


@override_settings(REGISTER_WATCH_FETCH=True)
def test_the_health_of_the_watch_for_production_status():
    from app.core import production_status as status_module

    # The test database applied the watch's migrations moments ago: first-run grace.
    assert watch.watch_health() == watch.Health(True, "not run yet")
    _ran(started_hours_ago=10, targets_checked=300, errors=[{"viga": "x"}], ai_failures=2)
    assert watch.watch_health() == watch.Health(True, "1 errors, 2 AI failures")
    row = status_module._register_watch()
    assert (row.key, row.label, row.ok) == ("register_watch", "Register watch", True)

    WatchRun.objects.all().delete()
    _ran(started_hours_ago=31, targets_checked=300)
    assert watch.watch_health().detail == "no finished run in 30 h"

    WatchRun.objects.all().delete()
    _ran(started_hours_ago=2, targets_checked=0, errors=[{"viga": "a"}, {"viga": "b"}])
    assert watch.watch_health().detail == "last run read no source (2 errors)"

    WatchRun.objects.all().delete()
    _ran(started_hours_ago=1, finished=False)  # tonight's run, still going
    assert not watch.watch_health().ok  # nothing finished in 30 h before it
    _ran(started_hours_ago=20, targets_checked=300)
    assert watch.watch_health().ok


@override_settings(REGISTER_WATCH_FETCH=True)
def test_a_watch_switched_on_that_never_ran_turns_red_after_its_first_night(monkeypatch):
    """QA-NEW-05 (coordinator QA 2026-11-20): an enabled watch whose scheduled run
    never happened (a cron line never installed, a host rebuilt without it) was
    green «never run» for ever. It has `STALE_AFTER` from the release that
    switched it on; after that it is red, and Haldus says why."""
    from django.utils import timezone

    from app.core import production_status as status_module

    assert not WatchRun.objects.exists()
    now = timezone.now()
    monkeypatch.setattr(watch, "watch_activated_at", lambda: now - dt.timedelta(hours=29))
    assert watch.watch_health(now) == watch.Health(True, "not run yet")

    monkeypatch.setattr(watch, "watch_activated_at", lambda: now - dt.timedelta(hours=31))
    health = watch.watch_health(now)
    assert (health.ok, health.detail) == (False, "never run")
    assert health.problem
    assert status_module._register_watch().ok is False

    # No record of the release at all is no reason to stay green either.
    monkeypatch.setattr(watch, "watch_activated_at", lambda: None)
    assert watch.watch_health(now).ok is False

    # The first finished run settles it.
    _ran(started_hours_ago=2, targets_checked=300)
    assert watch.watch_health().ok


def test_the_watch_knows_when_this_database_switched_it_on():
    activated = watch.watch_activated_at()
    assert activated is not None


@override_settings(REGISTER_WATCH_FETCH=True)
def test_a_run_killed_from_outside_is_stopped_once_it_is_older_than_any_run(client, specialist):
    from app.core import production_status as status_module

    _ran(started_hours_ago=26, targets_checked=300)
    killed = _ran(started_hours_ago=4, finished=False)

    assert killed.aborted and not killed.running
    row = status_module._register_watch()
    assert (row.ok, row.detail) == (False, "last run stopped before its end")
    client.force_login(specialist)
    page = client.get(reverse("haldus:register_watch")).content.decode()
    one = client.get(reverse("haldus:register_watch_run", args=[killed.pk])).content.decode()
    assert "Katkes" in page and "Viimane kontroll katkes." in page and "Vigadeta" not in page
    assert "Katkes" in one and "Käib" not in one


def test_fetching_switched_off_is_green():
    assert watch.watch_health() == watch.Health(True, "off")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_only_one_run_at_a_time(registers):
    import threading

    from django.core.management.base import CommandError
    from django.db import connection

    from app.core.errors import DomainError

    holding, release = threading.Event(), threading.Event()

    def other_run():
        with watch._one_run_at_a_time():
            holding.set()
            release.wait(10)
        connection.close()

    thread = threading.Thread(target=other_run)
    thread.start()
    try:
        assert holding.wait(10)
        with pytest.raises(DomainError, match=watch.ALREADY_RUNNING):
            watch.run([], use_ai=False, today=TODAY)
        with pytest.raises(CommandError, match=watch.ALREADY_RUNNING):
            call_command("watch_registers", "--no-ai")
        assert not WatchRun.objects.exists()
    finally:
        release.set()
        thread.join(10)
    assert watch.run([], use_ai=False, today=TODAY).finished_at is not None


@override_settings(REGISTER_WATCH_AI=True)
def test_the_model_is_asked_in_batches_and_a_cut_answer_costs_one_batch_only():
    matter = _bill_teema()
    first = _finding(matter)
    findings = [first] + [
        WatchFinding.objects.create(
            target=first.target,
            matter=matter,
            external_id=f"doc:{n}",
            title=f"Dokument {n}",
            occurred_on=dt.date(2026, 11, 6),
        )
        for n in range(59)
    ]

    class CutSecond(FakeClient):
        def _create(self, **request):
            response = super()._create(**request)
            if len(self.sent) == 2:
                response.stop_reason = "max_tokens"
            return response

    client = CutSecond(["RELEVANT"] * ai.BATCH)
    usage = ai.Usage()

    judged = ai.judge(matter, findings, usage, client=client)

    sizes = [
        len(json.loads(r["messages"][0]["content"].split("Documents found:\n", 1)[1]))
        for r in client.sent
    ]
    assert sizes == [25, 25, 10]
    assert judged == 35 and usage.failures == 1 and usage.calls == 3
    assert all(r["max_tokens"] == ai.MAX_TOKENS for r in client.sent)


def test_a_handed_over_teema_takes_its_notice_to_the_new_owner(specialist, other_specialist):
    from django.utils import timezone

    from app.matters.services import assign_matter
    from app.register_watch import notices
    from app.register_watch.models import RegisterNotice

    matter = _bill_teema(owner=specialist)
    _finding(matter)
    notices.notify(matter, 1)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    assert notices.due_unread(specialist).count() == 0
    assert notices.due_unread(other_specialist).count() == 1
    notices.notify(matter, 2)
    row = RegisterNotice.objects.get(matter=matter)
    assert (row.recipient_id, row.documents) == (other_specialist.pk, 3)

    arrived = notices.arrived_at(row)
    RegisterNotice.objects.filter(pk=row.pk).update(updated_at=arrived - dt.timedelta(days=5))
    row.refresh_from_db()
    before = notices.arrived_at(row)
    assert notices.mark_read(row)
    row.refresh_from_db()
    assert notices.arrived_at(row) == before  # reading is not an arrival
    assert row.read_at is not None and row.read_at > before
    assert timezone.now() - row.read_at < dt.timedelta(minutes=1)


# -- the one door, tested below its switch (TEST-06) --------------------------------


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.asked: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        self.asked.append(size)
        return self.body if size is None or size < 0 else self.body[:size]


class _Opener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.urls: list[str] = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def door(monkeypatch, settings):
    """`http` with the switch on and the network replaced; no time passes."""
    settings.REGISTER_WATCH_FETCH = True
    monkeypatch.setattr(http, "_last_call", {})
    clock = {"now": 1000.0, "slept": []}
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", lambda s: clock["slept"].append(round(s, 2)))

    def use(outcome):
        opener = _Opener(outcome)
        monkeypatch.setattr(http, "_OPENER", opener)
        monkeypatch.setattr(http, "_FORM_OPENER", opener)
        return opener

    return SimpleNamespace(use=use, clock=clock)


def test_the_door_never_follows_a_redirect():
    import urllib.request

    handler = http._NoRedirect()
    request = urllib.request.Request("https://api.riigikogu.ee/api/volumes/drafts")
    assert (
        handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/")
        is None
    )
    for opener in (http._OPENER, http._FORM_OPENER):
        redirects = [
            h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        assert redirects and all(isinstance(h, http._NoRedirect) for h in redirects)


def test_an_answer_over_the_cap_is_refused_unread(door):
    big = door.use(_Response(b"x" * (http.MAX_BYTES + 10)))
    with pytest.raises(FetchError, match="liiga suur"):
        http.get_json("api.riigikogu.ee", "/api/volumes/drafts")
    with pytest.raises(FetchError, match="liiga suur"):
        http.get_text("adr.rik.ee", "/ram/")
    assert big.outcome.asked == [http.MAX_BYTES + 1, http.MAX_BYTES + 1]


def test_http_and_network_failures_are_fetch_errors_with_safe_words(door):
    import urllib.error

    door.use(urllib.error.HTTPError("https://x", 503, "secret body", {}, None))
    with pytest.raises(FetchError, match=r"^api\.riigikogu\.ee vastas 503$"):
        http.get_json("api.riigikogu.ee", "/api/volumes/drafts")
    with pytest.raises(FetchError, match=r"^adr\.rik\.ee vastas 503$"):
        http.post_form("adr.rik.ee", "/ram/", {"a": "b"})
    door.use(urllib.error.URLError("name lookup failed"))
    with pytest.raises(FetchError, match=r"^dok\.hm\.ee ei vastanud \(URLError\)$"):
        http.get_json("dok.hm.ee", "/api/v1/documents.json")
    door.use(TimeoutError())
    with pytest.raises(FetchError, match=r"ei vastanud \(TimeoutError\)"):
        http.get_text("adr.rik.ee", "/ram/")


def test_calls_to_one_host_are_spaced(door):
    door.use(_Response(b"{}"))
    http.get_json("api.riigikogu.ee", "/api/a")
    http.get_json("api.riigikogu.ee", "/api/b")
    http.get_json("dok.hm.ee", "/api/c")
    door.clock["now"] += 10
    http.get_json("api.riigikogu.ee", "/api/d")

    assert door.clock["slept"] == [http.SPACING_SECONDS["api.riigikogu.ee"]]
