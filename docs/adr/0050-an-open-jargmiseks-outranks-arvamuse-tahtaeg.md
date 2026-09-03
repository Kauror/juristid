# ADR 0050 — An open `Järgmiseks` outranks `Arvamuse tähtaeg` in the work model

- Status: accepted
- Date: 2026-08-30
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0031 §5 (why an empty `Arvamuse tähtaeg` means *no commitment*),
  ADR 0033 (`?too=` makes the dated-work populations addressable),
  ADR 0046 and ADR 0049 (the deadline panels that read this model)

## Context

`Matter.response_deadline` — *Arvamuse tähtaeg* — became the third source of the
shared work model, so a Matter carrying nothing but a deadline stopped falling
off every dated surface. That was right, and it is not what this record changes.

What it left behind is a precedence question nobody had answered. Both of these
are dated obligations on one Matter, and until now both were live work at the
same time:

```
Arvamuse tähtaeg:   27.01.2026
Järgmiseks:         JÄLGIN · Vaata uuesti üle 09.10.2026
```

A lawyer had read that file, decided it was not theirs to act on yet, and
written down when to look again. The work model still called 27.01 an
outstanding obligation, so in October the Matter was **eight months overdue** on
Minu asjad and on Osakond's Seis strip — on the strength of a date the person
carrying it had already superseded.

That is worse than noise. *Üle tähtaja* is the figure a department head reads
first, and a page that reports a file as late when its owner has said what is
happening to it teaches people to discount the number. The register's own
`ARVAMUSE TÄHTAEG` column is the commonest date on the corpus, so this was not a
rare shape.

## Decision

### The hierarchy

For one Matter, the dated obligation that counts as **current operational work**
is decided in this order:

1. **An open `NextAction` exists** → the `NextAction` is the operational work.
2. **No open `NextAction`, and an outstanding `response_deadline` exists** →
   `Arvamuse tähtaeg` is the fallback deadline.
3. **Neither** → the Matter has no dated operational work from either source.

Stated as a sentence: *Arvamuse tähtaeg is the deadline a file carries until
somebody says what happens next.* An open `Järgmiseks` is somebody saying it.

### It is not a comparison of dates

Any open action wins, **including one dated later than the response deadline,
and including one with no date at all**. There is no chronological rule here and
deliberately so:

* picking the earlier date would put the worked example straight back — January
  would beat October, and the file would be overdue again;
* an undated WAIT or MONITOR is still a decision. "I am waiting on the ministry
  and I do not yet know when to look again" is a stronger statement about
  today's work than a date nobody has revisited.

### It is not a judgement about the action

`DO`, `WAIT` and `MONITOR` all count. So does an action materialised by
current-register enrichment: that carries the register's own structured
`JÄRGMISEKS` value, which is the department's instruction as much as a typed one
is. There is deliberately no second idea of a "sufficiently human" NextAction —
one would be a new concept to keep in step, and the first argument about which
actions qualify would be unanswerable.

### The stored column is never touched

`Matter.response_deadline` remains canonical Matter data. This precedence
**reads**; it does not write. Nothing is cleared, moved, completed, superseded,
converted into a `NextAction` or backfilled, and no replacement deadline record
is created. The Matter header still states the date as the fact it is:

```
header      Tähtaeg 27.1.2026        ← what the register recorded
work model  JÄLGIN · 09.10.2026      ← what needs attention now
```

Those two lines are not in conflict. They answer different questions, and the
header is the place a superseded commitment stays visible.

### The fulfilment rule is unchanged

A response deadline is fallback work only when **all** of these hold:

- the Matter is open and `FULL`;
- `response_deadline` is not null;
- no `SENT` Submission exists on the Matter;
- no open `NextAction` exists on the Matter.

The third condition is the rule the response-deadline work landed with; the
fourth is this record.

> **Amended by ADR 0059 (2026-09-03).** A fifth condition joined the list: the
> Matter's **current** register state must not record the opinion work as
> finished — `VÄLJA` reading either a date or *ei saatnud*. A blank cell and an
> unreadable one both leave the deadline outstanding. That rule discharges the
> *operational* obligation only: it creates no `Submission`, claims no evidence
> about what was sent, and enters no opinion statistic. The precedence this
> record establishes is unaffected — an open `NextAction` still outranks the
> deadline whatever the register says — and `response_deadline` is still never
> written.

### `Järgmise tegevuseta` does not move

*Järgmise tegevuseta* (`?tegevus=puudub`) still means exactly *no open
NextAction*. A response deadline is not a stored `NextAction` and does not
become one, so a Matter with a deadline and no instruction is still counted
there. Whether that population should also consider the deadline is a separate
product question and is not decided here.

### `Oluline tähtaeg` is independent

The precedence is between a Matter's own `response_deadline` and its one open
`NextAction`. A `MatterImportantDate` is a third business fact — a consultation
closing, a transposition deadline — and a Matter may legitimately carry a
current instruction and several milestones at once. Milestones are not
suppressed.

## Implementation

One place: `outstanding_response_deadlines` in `app/matters/work_items.py`, the
canonical population every dated surface already reads. Minu asjad, Osakond's
Seis strip, *Vajab sekkumist*, *Eesolev* and the register's `?too=` populations
needed no change of their own, which is the whole reason that model exists.

The test is an `Exists` subquery beside the fulfilment one, so the source stays
a constant number of queries however many Matters it holds — measured unchanged
at 1, 20 and 60 Matters, on both the shared read and the Osakond page.

Both subqueries are **reader-blind**, as the fulfilment test already was. Each
can only ever remove a row, so neither can widen what anybody sees and a
restricted child cannot be read through the difference: what changes is whether
one date is called work, never whether a hidden record is disclosed. Scoping
them would be actively worse — it would make one colleague's deadline live and
another's suppressed, which is two answers to a question about the Matter rather
than about the reader.

## Alternatives considered

**Compare the two dates and take the earlier.** It is the obvious rule and it is
wrong: it reinstates the exact defect, because the superseded deadline is
usually the earlier one. It also treats a date the register supplied as
evidence about today's work, which it is not.

**Only let a `DO` outrank the deadline.** It would leave the worked example
broken — the case that prompted this was a `MONITOR` — and would imply that
waiting is not a decision.

**Ignore actions created by the register import.** It would need a new notion of
which actions are real, applied to a value that is already the department's own
structured `JÄRGMISEKS`.

**Clear `response_deadline` when an action is recorded.** It destroys a canonical
register fact to express a reading, and the header would lose the date a lawyer
still needs to see. A read model exists precisely so this is not necessary.

## Consequences

- Files under an explicit instruction leave the overdue and this-week
  populations. Counts fall, and the drill-through behind each falls with it —
  asserted by Matter id rather than by total.
- The precedence is evaluated on every read, so ending the only open action
  restores the fallback immediately: no write, no backfill, no rebuild.
- No model change, no migration, no data change.
- `app/matters/dashboard.py`'s `upcoming_rows` and `attention_items`, and
  `app/matters/selectors.py`'s `my_attention_items`, read `response_deadline`
  operationally and do **not** apply this precedence. They are the pre-v2
  Ülevaade read models and no view or template reaches them today. They are left
  as they are rather than changed blind; if any of them is ever put back on a
  surface, it must adopt this rule rather than acquire an exception of its own.

## Reversibility

Total, and in one place. The rule is two clauses of one queryset; removing them
restores the previous behaviour exactly, because nothing was written to express
it.
