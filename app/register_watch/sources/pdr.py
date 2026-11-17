"""Ministry document registers on the HiA PDR software, searched by title.

The Ministry of Education and Research (`dok.hm.ee`) and the Ministry of Climate
(`adr.envir.ee`) publish their registers through the same JSON search:
``/api/v1/documents.json?title=…&sortBy=regDate&sortDir=desc``. The date filter
the form offers is ignored by the API, so the newest page is read and the rest
is left to the next night.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.register_watch.sources.http import FetchError, get_json
from app.register_watch.sources.items import Item, parse_day

PAGE = 50


@dataclass(frozen=True)
class Register:
    host: str

    def document_url(self, uuid: str) -> str:
        return f"https://{self.host}/et/document.html?id={uuid}"


HTM = Register(host="dok.hm.ee")
KLIM = Register(host="adr.envir.ee")


def search(register: Register, text: str) -> list[Item]:
    """The newest documents whose title contains ``text``."""
    data = get_json(
        register.host,
        "/api/v1/documents.json",
        {
            "title": text,
            "offset": 0,
            "limit": PAGE,
            "sortBy": "regDate",
            "sortDir": "desc",
            "fields": "id,regCode,regDate,title,extOrganization",
        },
    )
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise FetchError(f"{register.host} vastus on võõra kujuga")
    items: list[Item] = []
    for row in data["data"]:
        key = row.get("id")
        title = (row.get("title") or "").strip()
        if not key or not title:
            continue
        detail = " · ".join(
            part
            for part in (
                (row.get("regCode") or "").strip(),
                (row.get("extOrganization") or "").strip(),
            )
            if part
        )
        items.append(
            Item(
                external_id=f"doc:{key}",
                title=title[:500],
                occurred_on=parse_day(row.get("regDate")),
                url=register.document_url(key),
                detail=detail[:300],
            )
        )
    return items
