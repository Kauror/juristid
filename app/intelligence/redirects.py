"""Where the three retired reading pages send a reader now.

Three generated department-wide lists used to live at these addresses — Olulised
tähtajad, Jõustuvad aktid and Töövõidud — each one showing the Matters that
carry one structured fact. They are not destinations any more: a Teema is found
in the register, and an `Oluline tähtaeg` is its owner's own upcoming work
(docs/adr/0071).

Every address still resolves, because a bookmark, a pasted link and a message
from three months ago are all real. Each one lands where its question is
answered:

===============================  ==========================================
``/jalgimine/toovoidud/``        ``/teemad/?toovoit=on`` — the register,
                                 narrowed to files carrying a Töövõit.
``/jalgimine/joustumised/``      ``/teemad/?joustumine=on`` — the same, for
                                 files carrying a Jõustumine.
``/jalgimine/tahtajad/``         ``/minu-asjad/`` — an important deadline is
                                 personal work, so it goes to the reader's
                                 own queue rather than to a list of
                                 everybody's.
===============================  ==========================================

**The old query strings are dropped, and that is the honest answer rather than
a shortfall.** ``?suund=moodunud``, ``?allikad=joustumised``, ``?aasta=2024``
and ``?staatus=`` described windows over a list of *facts*; the register lists
*Matters*, and there is no translation of "commencements that have passed" into
a narrowing of it that means the same thing. Carrying the parameters across
would leave words in the address that nothing reads — and, worse, that the
register's own search box would then carry forward as hidden inputs, so a reader
would be sharing links containing a filter that was never applied. The brief for
this change says it in one line: where an old filter cannot be represented
exactly, do not fabricate an approximation.

What is lost is therefore exactly one thing per page: the *window*. The
population is preserved.

``302``, not ``301``. A permanent redirect is cached by the browser and is
effectively irreversible; these three destinations are a product decision that
is one month old, and pinning them into everybody's cache is not a cost worth
paying for a page nobody opens from an address bar. The two legacy routes
(``/olulised-tahtajad/`` and friends) were 301 before and become 302 for the
same reason — they now point somewhere new, so the old permanent answer is
wrong anyway.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from app.matters.register_filters import (
    COMMENCEMENT_PARAM,
    FACT_PRESENT,
    VICTORY_PARAM,
)


def _register(parameter: str) -> str:
    """The register, narrowed to files carrying one structured fact.

    **``?olek=koik`` is load-bearing and is the whole reason this is a function
    rather than a literal.** Neither retired page had an open/closed dimension
    at all: Töövõidud listed every confirmed victory there had ever been, and
    Jõustuvad aktid every active commencement. The register's own default is
    *avatud*, so omitting the parameter would silently drop every fact on a
    closed file — which, for a work victory, is most of them. A redirect that
    quietly shows a fraction of what the bookmark used to show is worse than
    one that 404s, because nothing on the page says anything is missing.

    Preserving the population is what a redirect owes; the *window* is what
    cannot be preserved and is documented as lost at the top of this module.
    """
    return f"{reverse('matters:matter_list')}?{parameter}={FACT_PRESENT}&olek=koik"


def work_victories(request: HttpRequest) -> HttpResponse:
    """Töövõidud, as a narrowing of the register."""
    return redirect(_register(VICTORY_PARAM))


def effective_dates(request: HttpRequest) -> HttpResponse:
    """Jõustuvad aktid, as a narrowing of the register."""
    return redirect(_register(COMMENCEMENT_PARAM))


def important_dates(request: HttpRequest) -> HttpResponse:
    """Olulised tähtajad, to the reader's own upcoming work.

    Minu asjad for somebody who is signed in as a person, and the register for
    anybody else. Behind the shared gate with no persona chosen there is no
    "minu" to show, and ``matters:my_work`` is ``login_required`` — so sending
    that reader there would bounce them to the persona page with no explanation
    of why the link they followed did not open. The register is a real
    destination for them, and it is where the same Matters are.
    """
    if request.user.is_authenticated:
        return redirect(reverse("matters:my_work"))
    return redirect(reverse("matters:matter_list"))
