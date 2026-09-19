"""Titles the browser suite files, and the words it may not build them from.

Separate from `e2e/conftest.py` — which cannot be imported without Playwright —
so `tests/test_e2e_title_namespacing.py` can prove the rule below in the fast
PostgreSQL suite rather than in whichever browser shard happens to hold it.
"""

from __future__ import annotations

import uuid

#: Words a neighbouring browser file types into the register and then asserts an
#: **exact** row count for. A Matter filed under one of these is a second answer
#: to that search, and the file that filed it is not the file that fails.
#:
#: `Tavaline` is the measured case. `e2e/test_register_search.py` seeds
#: «Tavaline avatud teema kõigile nähtav», types `Tavaline`, and asserts one row
#: in four places; `e2e/test_lawyer_classification_round.py` used to file
#: «Tavaline saabunud eelnõu …» and made that two whenever the partition put the
#: two files in one shard. Looking a seeded title *up* by these words is fine
#: and common — what this forbids is creating a new row that answers to one.
RESERVED_REGISTER_WORDS = ("Tavaline",)


def unique_title(prefix: str) -> str:
    """A title no other row in this world can be carrying.

    The browser suite runs against **one seeded database per shard**, shared by
    every file the shard was given and never reset between them
    (`ci_sharding.py`, .github/workflows/ci.yml). So a fixed title is not an
    identity: a file that runs twice against the same world — a rerun, a local
    loop — files a second Matter under the same name, and every locator that
    asks for it by name then resolves to two and raises in strict mode.

    A test that has to find its own row afterwards asks for one of these instead
    of writing a constant. Short on purpose: the register's title column clips,
    and the token has to survive being read back out of a cell.

    The prefix is refused if it carries a `RESERVED_REGISTER_WORDS` word, for
    the same reason the world being shared makes a fixed title unsafe: one
    file's new row is the next file's extra search result, and the search is
    over the title. Matched as a substring, case-folded, because the conservative
    reading is the cheap one — nothing legitimate here is spelled «tavaline».

    The check is deliberately only this. A title built some other way — filled
    straight into `#id_title` — is not seen by it, and the rule is restated as
    prose where that happens (`e2e/test_next_step_arrival.py`,
    `e2e/test_work_surfaces_review.py`).
    """
    for word in RESERVED_REGISTER_WORDS:
        if word.casefold() in prefix.casefold():
            raise ValueError(
                f"«{word}» is reserved: a neighbouring browser file searches the "
                f"register for it and asserts an exact row count, and the browser "
                f"world is shared. Namespace {prefix!r} on something of this "
                f"file's own."
            )
    return f"{prefix} {uuid.uuid4().hex[:8]}"
