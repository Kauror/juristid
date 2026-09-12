# Uuendused — the user-facing release notes

`uuendused.toml` in this directory is the whole source of `/uuendused/`. It is
read by `app/core/release_notes.py`, rendered by
`templates/core/release_notes.html`, and reached from the `Uuendused` link in
the footer.

There is no database table, no migration, no admin screen and no GitHub call at
request time. The file is baked into the image beside the code it describes and
parsed once per process.

## The rules

**One entry per calendar day.** The parser refuses a second `[[day]]` for a date
that is already described. Several production releases on one day belong
together: what a lawyer wants to know is what was different the next time they
opened the application, not which of that day's three images carried it.

**The date is the day the change became available in production** — not the
commit date, not the branch date, not the day the pull request was opened. That
is the only one of the four that is an event in a reader's life.

**Write for the Chamber's lawyers.** One sentence per bullet, plain Estonian,
what is different when *they* use Juristid:

> Jõustumise ja töövõidu saab lisada otse teema lehel.

not

> Implemented HTMX-based inline intelligence fact creation.

**No pull request numbers, branch names, commit SHAs or component names in the
bullets.** They belong in the entry's metadata, which is never rendered.

**Not everything gets a bullet.** CI changes, tests, refactors, dependency
bumps, ADRs, runbooks, migration mechanics and container topology do not — a day
whose whole payload was that kind of work simply has no entry, and several days
in the backfill are exactly that. Operational work that *makes an advertised
feature actually work* is folded into that feature's bullet rather than getting
one of its own.

**There is no CI rule that a pull request must edit this file**, deliberately.
Blocking a one-line internal fix on a release note nobody would read is how a
file like this fills up with noise.

**There is a release-time rule instead.** A pull request is not what reaches a
lawyer; a release is, and it is the one moment every change passes through. So
`.github/workflows/release-image.yml` — the only channel to production — takes
the commit production runs now (`previous_sha`) beside the commit being built,
lists the paths between them, and asks `scripts/ci/assert_release_note.py` one
question: does this payload need an entry here, and does it have one?

| the payload | the answer |
| --- | --- |
| `uuendused.toml` changed | `note-present` — build |
| nothing under `app/`, `templates/`, `static/`, `config/` or `manage.py` changed | `internal-only` — build; no note is owed, which is the per-PR rule kept at release size. A rebuild of the running revision is this case. |
| the application changed and this file did not | `missing` — the build stops and names the paths |
| the same, with `release_note_waiver` set | `waived` — build; the reason is written into the release manifest beside the digest |

The waiver is for a payload that touches the application and changes nothing a
reader meets — a logging line, a query made cheaper. It is not for «I will
write it later»: the entry takes a minute, and the manifest that names the
waiver outlives the run.

Every answer the gate can give is held in `tests/test_release_note_gate.py`,
over lists of paths and no repository.

**What the gate proves, and what it does not.** The gate answers one mechanical
question: did `uuendused.toml` change in a payload a reader will notice? It does
not read the entry, and it cannot tell whether every user-facing change in the
payload is described, described accurately, or described under the right day.
That judgement stays with release review: before running the workflow, read the
payload (`git diff --stat <previous_sha> <sha> -- app templates static config`)
against the day's `changes` and add what is missing. A semantic changelog check
in CI — the code diffed against the prose — is deliberately not attempted: it
would be either wrong often enough to be ignored or strict enough to fill this
file with noise, which is the failure the per-PR rule above already refuses.

## Adding to it

A new day, at the top of the file (order in the file does not matter — the
renderer sorts newest first — but keeping it chronological makes the diff
readable):

```toml
[[day]]
date = 2026-09-14
basis = "deployment"
revisions = ["<the production revision>"]
prs = [155]
changes = [
  "Üks lause selle kohta, mis on kasutaja jaoks teisiti.",
]
```

Another bullet on a day that already exists: add a string to that day's
`changes`. The count in the accordion summary follows by itself.

`revisions` and `prs` are optional maintainer notes. They are parsed and
ignored, so a later reader can find out which release an entry came from without
that ever reaching a page.

## `basis`

How the day was established. Never rendered.

| value | meaning |
| --- | --- |
| `deployment` | a release report, a production snapshot or a recorded smoke test names that revision live on that day. The default, and what a new entry should be. |
| `release-image` | the production image for that revision was built for deployment that day (`.github/workflows/release-image.yml`), which is this project's release channel and is used for nothing else. |
| `merge` | **backfill fallback only.** The day the change reached `main`, where no deployment day could be established at all. Do not use it for a new entry — if you are writing a note about something you just released, you know the day. |

The current file uses `deployment` and `release-image` and has no `merge`
entries; the value exists so that a future backfill cannot quietly pass a merge
date off as a deployment date.

## What the parser refuses

`app/core/release_notes.py` raises `ReleaseNotesError` — loudly, rather than
rendering the half of the history it could read — on a duplicate date, a date it
cannot parse, a day with no changes, an empty change, an unknown `basis` and a
file that is not valid TOML. `tests/test_release_notes.py` holds each of those.

## What the page does with it

Newest day first, and only the newest day open. Native `<details>`/`<summary>`,
so opening and closing needs no JavaScript. Bullets are rendered as plain text
through ordinary autoescaping — never `|safe` — so a stray `<` in a release note
is a `<`.
