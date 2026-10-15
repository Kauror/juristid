"""Independent QA 2026-10-14 — subdocs area: every file link a page draws must open.

Crawls the Teema page, Dokumendid and the opinions workspace for links into
`/dokumendid/…` and follows each one as the same persona. A link drawn to a file
the reader may not open, or to one that was taken off the Matter, is a broken
interaction (and, for a restricted file, a disclosure of its name). Probes
against main 81a7d6e4.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from django.urls import reverse

from app.matters.services import create_matter
from app.workflow.models import StageVocabulary
from tests.follow_ups import day, send_koja_arvamus

pytestmark = pytest.mark.django_db

FILE_LINK = re.compile(r'href="(/dokumendid/[^"#?]+)')


def _matter(owner, **extra):
    return create_matter(
        title="QA lingid",
        actor=owner,
        owner=owner,
        stage=StageVocabulary.objects.get(key="consultation"),
        **extra,
    )


def _pages(matter):
    return [
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        reverse("submissions:sent") + "?olek=KOIK",
    ]


def _file_links(client, matter) -> set[str]:
    links: set[str] = set()
    for page in _pages(matter):
        response = client.get(page)
        assert response.status_code == 200, page
        links |= {unquote(href) for href in FILE_LINK.findall(response.content.decode())}
    return links


def _broken(client, links) -> list[tuple[str, int]]:
    broken = []
    for link in sorted(links):
        status = client.get(link).status_code
        if status not in (200, 302):
            broken.append((link, status))
    return broken


def test_links_after_send_withdraw_and_removal_all_open(signed_in, specialist, organisation):
    matter = _matter(specialist)
    first = send_koja_arvamus(matter, specialist, [organisation], day(-3))
    send_koja_arvamus(matter, specialist, [organisation], day(-1))
    signed_in.post(reverse("submissions:withdraw", kwargs={"pk": first.pk}), {"reason": "QA"})
    signed_in.post(reverse("documents:remove", kwargs={"pk": first.final_version.document_id}))

    links = _file_links(signed_in, matter)

    assert links, "the pages drew no file link at all"
    assert _broken(signed_in, links) == []


def test_reader_is_drawn_only_links_it_can_open(client, reader, specialist, organisation):
    matter = _matter(specialist)
    send_koja_arvamus(matter, specialist, [organisation], day(-1))
    client.force_login(reader)

    links = _file_links(client, matter)

    assert links
    assert _broken(client, links) == []
