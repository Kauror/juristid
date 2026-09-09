# Teema page refinement — visual preview

**DO NOT MERGE. DO NOT DEPLOY.** This branch exists so the product owner can
open the proposed Matter page in a real browser and say yes or no. It is not the
production implementation, and the design handoff it renders does not authorise
removing anything the current page does — see
[the reconciliation report](matter-refinement-reconciliation.md).

Design source: `Site rebuild and usability pass (3).zip` →
`design-handoff/matter-page-refinement`, frame `#teema-ettepanek`.
Read at `origin/main` = `b3df3179c273d7c89f22db1d05d764f72e236151`.

## Look at it without running anything

`matter-refinement/A-side-by-side-1560x991.png` — current on the left, proposal
on the right, same Matter, same viewport, nothing scaled. The two halves are
also here full-size, `A-current-…` and `A-preview-…`.

## Run it

Needs the local PostgreSQL at `C:\CC\_pgsql18` and `uv`. From this worktree:

```powershell
pwsh preview-artifacts\preview.ps1 -Rebuild
```

`-Rebuild` drops and recreates the preview database, migrates it, seeds the
browser-suite world and then seeds one populated synthetic Matter. Leave the
flag off on later runs — the seed is idempotent and reseeding needs a fresh
database, because a Matter with history cannot be deleted (`ChangeEvent` is
append-only and the foreign keys are `PROTECT`, which is the product working
correctly).

The script prints three URLs when it finishes:

```
Sign in     http://127.0.0.1:8077/konto/arendus-sisselogimine/   (Mari Näidisjurist)
Current     http://127.0.0.1:8077/teemad/<matter>/
Preview     http://127.0.0.1:8077/disainisusteem/teema-refinement/<matter>/
```

Sign in first, then open the two page URLs side by side. They render the **same**
Matter, so every difference between them is presentation.

Its own database (`juristid_mrp`) and its own port (8077), so it cannot disturb
another session. Two processes can bind the same loopback port on Windows and
the older one keeps answering — the script always kills whatever holds 8077
first.

## Regenerate the artifacts

With the server up:

```powershell
uv run python preview-artifacts\capture.py --matter <uuid>       # 18 screenshots + responsive check
uv run python preview-artifacts\measure.py --matter <uuid>       # computed style vs 02-layout-spec.md
uv run python preview-artifacts\side_by_side.py                  # the comparison artifact
```

`capture.py` writes only into `preview-artifacts/matter-refinement/`. It does
not touch `e2e/baselines/` and nothing here is compared to a baseline.

## The preview is read-only

Every control on the page is the real production control, because seeing them is
the point. None of them writes: a script blocks form submission and HTMX inside
the preview root. The design defines appearance and open/close and has no
saving, validation or refusal state anywhere in it — and a save that succeeded
here would swap the *current* Matter view's markup into the middle of the
refined page.

Interactions that do work: clicking the `Järgmiseks` row toggles the composer
(the one script the refinement adds), every `details` opens and closes, fact-row
actions appear on hover and on keyboard focus, and inline-edit underlines appear
on hover.

## What is in the branch

| Path | |
|---|---|
| `app/core/design_preview.py` | the view, and its two gates |
| `app/core/urls.py` | **one conditional route**, registered only under `DEBUG` |
| `app/core/management/commands/seed_matter_refinement_preview.py` | the synthetic Matter |
| `templates/design_preview/` | the page and five partials |
| `static/css/matter_refinement_preview.css` | every visual rule, scoped under `.matter-refinement-preview` |
| `static/js/matter_refinement_preview.js` | the row-click toggle, and the write guard |
| `tests/test_matter_refinement_preview.py` | the gate, the copy contract, and that `/teemad/` is untouched |
| `preview-artifacts/` | this directory |

**No production template, stylesheet or script was edited.** The only production
file that changed is `app/core/urls.py`, which gains one route inside
`if settings.DEBUG and not settings.REAL_DATA_ALLOWED:`. `/teemad/<pk>/` is
therefore byte-identical to `main`, and a test asserts it.

No migration. No schema, search or archive change.

## Screenshots

| | |
|---|---|
| `A-side-by-side-1560x991.png` | **the comparison** — current vs preview |
| `A-side-by-side-full-page.png` | the same two, whole page |
| `A-current-…` / `A-preview-…` | the halves, full resolution |
| `B-preview-1920 / 1440 / 1280` | wide |
| `C-preview-1024 / 768 / 390` | stacked |
| `D-01-default` | the populated page |
| `D-02-composer-open` | opened by clicking the Järgmiseks row |
| `D-03-header-inline-editor-open` | Hetkeseis |
| `D-04-action-menu-open` | the ⋯ menu |
| `D-05-timeline-collapsed` | Ajajoon closed |
| `D-06-related-materials-open` | the rail's `Lisa`, with real suggestions |
| `D-07-factrow-actions-visible` | hover on a fact row |

## Throwing it away

Delete the branch. The only thing to unpick in a production implementation is
the `app/core/urls.py` hunk; everything else is files that exist nowhere else.
