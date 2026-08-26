# ADR 0035 — Who current business work may be assigned to

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

### 5. `Järgmiseks` with no person named still means the Matter's owner

Unchanged, including on a Matter whose owner has left. Leaving the field blank
is the record speaking, not somebody choosing; the fallback in
`set_next_action` is the approved semantics and this branch does not touch it.

An *explicitly* ineligible responsible person is an error and not "no value
supplied" — it is refused rather than quietly demoted to the owner, because a
rejected instruction silently becoming a different one would put a decision
nobody made into the audit trail.

### 6. A filter chooser narrows; the rows behind it do not

The `Vastutaja` selects on Teemad and on Statistika mean *current department
worker*, so they offer the current department workers — plus whoever the URL is
already filtered by, which may well be a departed colleague whose year the
report is about. Without that union the select would read `Kõik` on a page that
is filtering by somebody.

What is deliberately unchanged:

- the rows and aggregates, which still count every historical owner;
- `_display_value` in `app/reporting/filters.py` and `_filter_label` in
  `app/matters/views.py`, which resolve an owner identifier against the whole
  user table so a chip names a real person rather than reading `tundmatu`.

Narrowing a chooser is not permission to make a report lie.

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
