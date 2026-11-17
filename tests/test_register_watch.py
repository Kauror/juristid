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
