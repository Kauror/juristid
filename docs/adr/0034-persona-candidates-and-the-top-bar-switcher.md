# ADR 0034 — Who may be a persona, and switching without leaving the page

- Status: accepted
- Date: 2026-08-25
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0016 (authentication modes and the shared gate), ADR 0005
  (authorization and visibility inheritance), ADR 0009 (design tokens),
  ADR 0010 (browser testing)

## Context

ADR 0016 separated two things that look like one: the shared gate
**authenticates the door**, and the persona picked behind it is **a choice, not
a claim**. That separation held. What was built on top of it did not.

Two problems, one of them a security defect.

**The list was every active account.** `choose_persona` rendered
`User.objects.filter(is_active=True)` and `act_as` accepted a POST against the
same queryset. Every account in the system was therefore a persona somebody
could become: the technical administrator, a colleague given read-only sight of
the register, and anybody else who happened to have a login. Behind a shared
password that everybody in the department knows, "become the administrator" was
one form submission away — and the audit row it wrote would have read as work
somebody did.

That is the exact shape ADR 0005 refuses one layer down. `ROLES_WITH_RESTRICTED_ACCESS`
and `ROLES_WITH_BUSINESS_WRITE` both omit ADMINISTRATOR because *technical
administration is not business access*. The persona selector reintroduced
through the front door what those two sets close.

**Switching cost a page.** The only way to change persona was a link to
`/konto/kasutaja/`, choose, and be redirected to `Minu töö`. Somebody comparing
two colleagues' queues made a three-page round trip for each comparison, and
landed somewhere other than where they started every time. The page itself had
grown an amber warning, a paragraph of explanation, a role under every name and
a subheading — a page read once and skipped forever, in front of a list whose
only job was to be pressed.

## Decisions

### One central candidate population, read by the GET and the POST

`app/accounts/selectors.py` holds the rule:

```
active
AND role in {SPECIALIST, DEPARTMENT_HEAD}
AND NOT is_staff
AND NOT is_superuser
```

Both `choose_persona` (which renders the list) and `act_as` (which acts on a
submission) call `persona_candidates()`. This is the property that matters:
**the endpoint is the boundary, not the template.** Everybody behind the shared
door can post to `/konto/kasutaja/vaheta/`, so a row hidden in HTML is hidden
from a reader and from nobody else. A crafted POST carrying an administrator's,
a superuser's, a reader's or an inactive person's UUID is refused, and the
active persona is unchanged.

### Roles, never names

The rule reads `role`, `is_active`, `is_staff` and `is_superuser`, and nothing
that identifies a person. No name, no UPN, no address, no primary key.

This is not a stylistic preference. The department's people change; a
hard-coded colleague is a rule that has to be found and edited the day somebody
joins or leaves, by whoever notices — which is how a list quietly grows an
account that should not be on it. `tests/test_persona_candidates.py` asserts the
mechanism as well as the outcome: the same excluded account, renamed, is still
excluded; given a department role, it is included; and the selector's own source
is parsed to prove no person-identifying field reaches a filter.

The same reasoning already governs `is_department_head`, which is why
`Osakonna töö` is offered by role and not to a named colleague.

### `is_staff` and `is_superuser` are excluded regardless of role

Stricter than "the role decides", and deliberately so.

A technical grant is precisely what a crafted POST reaches for, and this
deployment keeps technical administration on separate accounts from business
work — `User.is_staff` is documented in the model as "ligipääs Django
haldusliidesele, mitte piiratud sisule". An administrator who genuinely does
departmental work holds a department role on their own account, which is the
shape the model is built for.

The cost is real and stated: granting a lawyer Django-admin access would take
them off the persona list. That failure is visible the same day and fixed by
moving the technical access to a technical account. The opposite failure — a
privileged account quietly becoming selectable — is noticed by nobody.

### A session that already holds an ineligible persona loses it

Narrowing the endpoint closes the door for new selections and does nothing about
the sessions that came through before it. The shared-gate session lasts twelve
hours, so an administrator persona chosen in the morning would go on being one
for most of a working day after the fix shipped.

`AuthenticationModeMiddleware._shared_gate` therefore checks the persona it
restored against the same `is_persona_candidate` predicate on every request, and
drops it if the rule no longer admits it: the persona goes, the gate stays open,
and the reader lands on the department view with nobody selected.

This is the treatment the middleware already gives a session whose gate expired,
for the same reason — a session must not go on acting as somebody it may no
longer act as. It is recorded through the existing `PERSONA_SELECTED` event with
`reason: "persona_no_longer_eligible"`, rather than vanishing silently: somebody
whose selection disappears mid-task should be able to find out why.

Cheap: the predicate reads the user object the auth middleware already loaded,
and adds no query.

### The audit mechanism is unchanged

`_record_persona_change` and `SecurityEventType.PERSONA_SELECTED` already
recorded the persona chosen, the previous one, the timestamp, the shared-gate
context and the request's address and user agent. No second audit path was
built. Choosing *Ilma kasutajata* is a persona change and is recorded as one,
with `chosen_persona: null`.

A refused switch writes **no** persona event. A refusal is not a change and
must not read as one later.

### Switching happens from the bar, and returns to the page

The pill on the top bar opens a popover holding the same candidate population.
Every choice is a `POST` with CSRF, carrying `request.get_full_path()` as
`next`, validated by the existing `_safe_next()` — which is `url_has_allowed_host_and_scheme`
against this host, so an external target falls back rather than redirecting.

Never a `GET` link. Persona selection mutates session state, and a link that
does is one a browser is free to prefetch and a crawler follows.

`act_as` now honours `next` for *Ilma kasutajata* too, where it previously
always redirected to Ülevaade. Landing somewhere else entirely is as
disorienting when the choice is *nobody* as when it is a colleague.

### The full page stays, and is only a list

`/konto/kasutaja/` keeps its route, its view and its name. It renders inside the
normal shell — bar, navigation, search, the pill — and carries a header band
with the title and who is currently selected, a section label counting the
people it actually shows, the rows, and the dashed *Ilma kasutajata* row.

Removed: the amber warning, the explanatory paragraph, the subheading, the role
under every name, and the address. The one sentence worth keeping —

> Valik ei ole autentimine — see muudab ainult, kelle vaadet näidatakse.

— survives in the popover footer, next to the control it is about rather than
above a list somebody has already scrolled past.

The whole row is the button, in a real form. Aiming at six letters on the right
of a 760px row is a target the design does not ask anybody to hit.

### `Minu töö` needs a *minu*

With no persona selected the navigation does not offer it, and the route already
refused it: `login_required` sends that reader to the persona page rather than
inventing somebody's personal queue. Ülevaade remains available — it is the
department's own dashboard and the reason somebody past the door has anything to
read before choosing a name.

`Osakonna töö` is unchanged: offered by `is_department_head` in the navigation,
enforced again by the view with a 404. This ADR adds the tests that pin it to
each persona rather than to a session.

### The browser suite gets a second server

The persona switcher exists only in `AUTH_MODE=shared_gate`; the routes 404 in
the other two, because a deployment that authenticates an individual has no list
of people somebody may become. The browser job therefore starts a second
`runserver` on port 8001 against the same database with the gate on, and the
persona tests point at it. `AUTH_MODE` is read per request from settings, so one
process cannot be both modes; converting the whole suite to type a password
before every unrelated test was the alternative, and it would have made every
existing test depend on a secret it does not care about.

## Alternatives considered

**Filter the list in the template only.** Rejected: it is not a boundary. This
is the defect being fixed, not a cheaper version of the fix.

**Include `is_staff` accounts that carry a department role.** Considered
seriously — it is the more forgiving reading, and the brief allows it. Rejected
because this repository's identity model already states that technical access is
separate from business access, and a persona list is the wrong place to start
blurring that.

**Narrow the owner and responsible-person pickers in the same change.** Rejected
for this PR. `active_users()` in `app/matters/forms.py` feeds four
`ModelChoiceField`s that are bound to existing values: narrowing the queryset
would make a Matter whose owner is outside the candidate set unsaveable, and
fixing that correctly means unioning the current value into each field. That is
a forms change with real regression surface, not a one-line one. It is recorded
as a finding for the authorization/write-boundary PR instead.

**Rename `Osakonna töö` to `Minu tiim`.** Out of scope. The access rule is what
this change is responsible for; the page's name is a product decision that
belongs with whoever is working on that surface.

**A menu/listbox ARIA pattern.** Rejected. The options are real submit buttons
in real forms, and `role="menuitem"` would replace the semantics the elements
genuinely have with a claim about a widget this is not. The pill is a disclosure
— `aria-expanded`, `aria-controls`, `aria-haspopup` — and the arrow keys, Escape,
click-outside and focus restoration are added by script over the buttons.

## Consequences

- Technical, administrative and read-only accounts cannot become a persona, by
  the endpoint and not by the page.
- The persona list is derived from roles, so it is correct the day the
  department changes without anybody editing code.
- Switching persona costs a click and keeps the page, which makes comparing two
  colleagues' queues a normal thing to do rather than a navigation exercise.
- One more process in the browser CI job, and one more log in its artifacts.
- Six new visual baselines. No existing baseline changes: the other two auth
  modes render the bar exactly as before.

## Reversibility

High. The selector is one module and two call sites; the switcher is one
template partial, one CSS block and one idempotent JS binder. Reverting the
strictness of the `is_staff` exclusion is one line in
`persona_candidates()` and one test. No migration, no data change.
