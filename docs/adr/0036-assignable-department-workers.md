# ADR 0036 — Who current business work may be assigned to

- Status: accepted
- Date: 2026-08-26
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0034 (who may be a persona), ADR 0005 (authorization and
  visibility inheritance), ADR 0016 (authentication modes and the shared gate),
  ADR 0021 (final register cutover — the historical owners this must not
  disturb)

## Context

ADR 0034 established that an account is not a colleague: administering the
system, being given read-only sight of the register, and existing so that
something can sign in are all reasons for an account that are not reasons to be
a person the department's work belongs to. It applied that to one surface — the
persona list — and closed a real defect there.

It did not apply it to the controls that hand out the work itself. At the
baseline this branch starts from, `app/matters/forms.py` still held:

```python
def active_users():
    return User.objects.filter(is_active=True).order_by("display_name")
```

and four owner/responsible controls read it, with three more populations
assembled independently in `app/matters/views.py` and `app/reporting/filters.py`
from the same too-broad filter. So the account that had just been refused as a
persona was still one dropdown away from owning a file, and — because two of
those fields are accepted by endpoints that never render them as a select — was
one crafted POST away even where no dropdown existed.

That is one rule written twice, and the two copies had already drifted. The
question is not whether the second copy is currently wrong. It is that nothing
fails when one of two copies is widened.

### The half that is easy to get wrong

Narrowing a chooser is a statement about **new** work. It is not a statement
about what old records say.

A Matter filed in 2019 may be owned by somebody who has since left, and that is
a true fact that the register, the department table, the timeline and every
report must go on stating. The naïve fix — point the form's queryset at the
current workers — breaks this in a way that does not announce itself: the owner
already on the Matter is no longer a valid choice, so an unrelated edit either
fails validation for a reason nobody can act on, or, the field being optional,
silently clears an owner nobody asked to remove.

## Decisions

### 1. One low-level definition, in `app/accounts/selectors.py`

`department_workers()` and `is_department_worker()` answer *is this a current
department worker*: active, role in `DEPARTMENT_WORK_ROLES`
(`SPECIALIST`, `DEPARTMENT_HEAD`), not `is_staff`, not `is_superuser`, ordered
by display name.

Nothing about the rule changed. ADR 0034's rule was lifted one level down and
given a name that says what it is about, so that more than one surface can read
it without either of them owning it.

### 2. Two named consumers, delegating — not two rules that agree

- `persona_candidates()` / `is_persona_candidate()` — whose work the
  application is showing.
- `assignable_business_users()` / `is_assignable_business_user()` — who current
  business work may be given to.

Both return the base rule unchanged. They exist so that a call site reads as the
question it is asking, and `PERSONA_ROLES` is now the same object as
`DEPARTMENT_WORK_ROLES` rather than a second frozenset with the same contents.

Persona behaviour is unchanged, and `tests/test_persona_candidates.py` is
unchanged except for one broadening: its grep-shaped "no name is written into
the rule" case now reads every function in the module rather than the three the
persona flow calls, because the code that decides anything moved.

### 3. Historical values are preserved by the *bound value*, not by widening

`assignable_including(*bound)` returns the current workers plus exactly the
people a record already names. That is the whole of the historical/current
distinction, in one function:

- an unchanged owner survives an unrelated edit;
- a *different* non-assignable account is still refused, because it is not in
  the queryset either;
- and preservation is per record — a departed colleague kept on the Matter they
  own is not thereby offered on any other Matter.

The same shape `MatterEditForm` already uses for retired Valdkonnad (ADR 0030
§7.2): validation accepts what the record carries, the chooser offers what may
be chosen today.

`MatterFieldForm` gained a `matter` keyword so the inline header control can do
this too. The header renders the Matter's owner as the selected option in that
control; without the union the endpoint would refuse the value it is displaying,
and pressing *Salvesta* having changed nothing would answer "Vigane väärtus."

### 4. Enforcement lives at the form boundary, not in the domain services

Considered and rejected: a guard inside `create_matter`, `assign_matter` and
`set_next_action`.

Those are not only the native UI's writers. `assign_matter` is also how
`app/legacy_import/owner_backfill.py` records ownership derived from register
cells — deliberately including departed colleagues, which is the point of that
run — and `set_next_action` is how `app/legacy_import/next_action_enrichment.py`
applies parsed register instructions, falling through to `matter.owner` for a
responsible person. A service-level predicate would refuse exactly the
historical facts those importers exist to preserve, and the usual escape hatch —
a `bypass=True` flag — is a rule with a documented way around it, which is not a
rule.

So the boundary is the five forms, each of which is the only way a person can
reach the corresponding write:

| surface | form | population |
| --- | --- | --- |
| Uus teema Vastutaja | `MatterCreateForm.owner` | assignable |
| Saabunud Vastutaja | `IncomingIntakeForm.owner` | assignable |
| Teema muutmine Vastutaja | `MatterEditForm.owner` | assignable + this Matter's owner |
| Teema päise kiirvahetus | `MatterFieldForm.owner` | assignable + this Matter's owner |
| Järgmiseks vastutaja | `NextActionForm.responsible` | assignable |

Every one of those is a `ModelChoiceField`, so the queryset is what validation
accepts and not merely what the template draws. `tests/test_work_assignment_eligibility.py`
posts a crafted identifier at each of the five, for each of the five shapes of
ineligible account, and asserts both that the request is refused and that
nothing was written.

Two of the fields are not rendered as a select anywhere —
`NextActionForm.responsible` and `MatterFieldForm.owner` on surfaces other than
the header — which is precisely why the queryset had to be narrowed rather than
the template.

**Revised in review.** The form boundary catches every value a request
*supplies*. It cannot catch a value the service *derives*, and `Järgmiseks` has
one — see decision 5. The answer is not a guard inside `set_next_action` after
all, and not a `bypass=` flag on it either: it is a second, named entry point
beside it, `set_next_action_for_new_work`, which the native callers use and the
importers do not. The distinction the service could never make — *is somebody
assigning work, or is something recording what was assigned* — is one every
caller already knows, and a separate function makes the caller state it.

The wrapper's signature carries the boundary in a way a comment could not: it
has no `provenance` parameter. That keyword exists for callers that are not a
person, and they are exactly the callers that must not come through here.

### 5. `Järgmiseks` with no person named means the owner — while the owner is still a colleague

`set_next_action` defaults `responsible` to `matter.owner`. That default is
correct for the caller it was written for and wrong for the ones that came
later, and the review found the seam.

An importer reconstructing a 2019 instruction with no named person is recording
a fact: it belonged to whoever held the file. A lawyer typing a next step into
the composer today is not recording anything — they are creating work. On a
Matter whose owner has left, the second one silently put the new step into a
departed colleague's queue, which is the one queue nobody opens. Historical
preservation and new assignment had been given one implementation because they
produce the same value in the common case.

So the rule is not *never a departed person*. It is **new assignments go to
current department workers**, and `responsible_for_new_work` in
`app/workflow/services.py` is where the two meanings separate. Three answers,
and deliberately no fourth:

| what the native request says | what happens |
| --- | --- |
| an explicit person who is assignable | accepted, unchanged |
| nobody named, owner is assignable | the owner — the approved convenience, untouched |
| nobody named, owner is not somebody work may be given to | refused, in Estonian, naming what the reader can fix |

Refused rather than repaired. Choosing the department head, the first name on
the list, or the person pressing the button would each be the system inventing
an assignment nobody made — and an invented assignment is indistinguishable from
a deliberate one a week later. Setting `responsible` to nobody is the same
failure with a blank where the name should be. The refusal says *Teema vastutaja
ei ole enam aktiivne osakonna töötaja. Määra teemale uus vastutaja, enne kui
järgmise sammu salvestad*, because changing who holds the file is the only thing
the reader can actually do: the step's own Vastutaja is not rendered on either
native surface.

Three native paths reach this, and all three go through the wrapper — the
composer (`compose_update`, which never sends a `responsible` at all and so
takes the fallback every single time), the header's `set_action`, and Uus teema.
A guard placed on `NextActionForm` alone would have left the busiest of them
open.

An unowned Matter is not this case. It has nobody to fall back *to*, its step is
stored with no responsible person exactly as before, and refusing it would have
retired working behaviour under cover of a fix.

An *explicitly* ineligible responsible person is an error and not "no value
supplied" — refused rather than quietly demoted to the owner, because a rejected
instruction silently becoming a different one would put a decision nobody made
into the audit trail. The forms already refuse it; the wrapper refuses it again,
so the next native caller inherits the rule instead of having to remember it.

`set_next_action` itself is unchanged, and its fallback is still the fallback.
`app/legacy_import/next_action_enrichment.py`, the seed commands and any future
provenance-bearing caller keep saying *this old instruction belonged to this old
colleague*, which is what they exist to do.

### 6. A chooser offers who may be given work; a filter offers who already has it

Also revised in review, and for the same underlying confusion as decision 5: two
controls that look alike were given one population.

A **chooser** hands out work. It offers the current department workers, and the
one exception `assignable_including` exists for — the particular owner a record
already names, so an unrelated edit cannot clear it.

A **filter** describes work that already exists. Narrowing it to the current
workers, plus whoever the URL happened to name, was too tight in a way that
hides exactly what somebody comes to the control looking for. Mart leaves
holding seventeen files awaiting handover; under the first reading Mart vanished
from the `Vastutaja` dropdown and was reachable only by a reader who already
knew Mart's UUID and was willing to construct a URL.

So a data filter offers:

> the current department workers, **union** the owners genuinely represented in
> the viewer's authorized population

implemented once, as `owner_filter_choices(population)` in
`app/accounts/selectors.py`. Current workers are on the list whether or not they
hold anything — filtering to a colleague and getting an honest empty page is a
useful answer.

**The population argument is the authorization boundary, and it is the caller's
job.** Never `User.objects.all()`, never every owner in the database. An option
is a name on a page, and a name that appears only because of records this reader
may not open would disclose the person, the fact that they hold something, and
that it is something the reader is not allowed to see. The two callers pass what
their own surface may show:

| surface | population |
| --- | --- |
| Teemad | `Matter.objects.visible_to(viewer)` |
| Statistika and the reports | `reporting_population(context)` — `visible_to` narrowed by `real_data()` |

Both are read **before** the surface applies its own owner filter. Derived from
the already-filtered queryset the select would offer exactly one name, the one
selected, and leave no way back to anybody else without editing the URL.
`reporting_population` was split out of `visible_matters` for that reason, so
there is still one definition of what the reports are built from.

A consequence worth stating plainly: an ADMINISTRATOR who genuinely owns a
visible Matter **does** appear in these filters. The register says they hold a
file, and a control that describes the register has to be able to say so. It
changes nothing about assignment — the chooser on the same page still refuses
them. Eligibility and representation are different questions, which is the whole
point of this section.

What is deliberately unchanged:

- the rows and aggregates, which still count every historical owner — a report
  count does not drop because somebody left;
- `_display_value` in `app/reporting/filters.py` and `_filter_label` in
  `app/matters/views.py`, which resolve an owner identifier against the whole
  user table so a chip names a real person rather than reading `tundmatu`. Those
  are pre-existing and out of this change's scope; they are noted as a follow-up
  because the option lists are now bounded by authorization and the chip labels
  are not.

Narrowing a chooser is not permission to make a report lie, and tidying a filter
is not permission to hide work.

## What this does not change

- **Minu töö and Osakonna töö.** Their populations, scopes and visibility rules
  are untouched. `CASEWORK_ROLES` in `app/matters/overview.py` and
  `app/matters/department_dashboard.py` still names the same two roles and is
  still unioned with everybody who currently owns something. It is deliberately
  a *wider* rule than this one — it is a report population, and a page about who
  holds what must not drop a row because the person holding it has left. Both
  definitions now carry a comment saying so, because the next reader will
  otherwise assume it is a copy that was missed.
- **The development sign-in page** (`app/accounts/views.py`), which offers
  synthetic accounts including the administrator, because signing in as one is
  what it is for.
- **`app/legacy_import/resolution.py`**, which resolves a name written in a
  register cell against every account that has ever existed. That is an identity
  lookup over history, not a chooser.
- **No user was created, deactivated, promoted or renamed**, and no business
  record was rewritten. There is no migration and no data migration; the change
  is entirely in which rows a queryset offers.

## Consequences

In the current production configuration the `Vastutaja` controls narrow from
four names to three. The account that leaves them is excluded by the generic
rule — an ADMINISTRATOR that also carries `is_staff`, refused on two independent
grounds — and not by any check on a name, an address, a UPN or an identifier.
The suite asserts that mechanism rather than that outcome: the same account,
renamed, is still excluded, and given a department role would be included.

The cost carried over from ADR 0034 is carried over here too: granting a lawyer
Django-admin access now takes them off the assignment lists as well as off the
persona list. That is one line in `department_workers()` to undo, it is visible
the same day, and the opposite failure — a privileged account quietly becoming
assignable — is neither.
