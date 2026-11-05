# OneNote export — read-only proof of concept

A bounded, read-only extractor that turns OneNote pages into a neutral archive
(the register-link comparison it once had is retired; see below).

**It is not an importer.** It creates no Matters, updates no register rows,
downloads no notebook in full and changes nothing in OneNote. The full
historical backfill is a later, separately authorised migration
(Stage-2B brief 62).

## Where it may be run

| | |
| --- | --- |
| Real notebook | **Only** on an approved Koda-controlled workstation |
| Output | Stays on that machine |
| The Unraid rehearsal server | Never — it has no notebook and must not fetch one |
| CI, this repository, pull requests | Synthetic fixtures only |

No page title, URL, attachment filename, body text, member or company name from
a real notebook may be committed, pasted into a pull request, or attached to an
issue. The tool's `ExportSummary` and the reconciliation summary are counts, and
counts are the only shape of a real result that may leave the machine
(Stage-2B brief 60, 84).

## What it needs, once

A public client application registration in the Koda tenant:

1. Entra admin centre → **App registrations** → *New registration*.
2. Any name. Supported account types: *Accounts in this organizational directory
   only*. No redirect URI is needed.
3. **Authentication** → *Advanced settings* → **Allow public client flows: Yes**.
   This is what permits the device-code flow, and it is why no client secret is
   ever created.
4. **API permissions** → *Add a permission* → Microsoft Graph → **Delegated
   permissions** → `Notes.Read`. Nothing else.
5. Grant admin consent for `Notes.Read` if the tenant requires it.

Copy the **Application (client) ID**. It is not a secret, and it is the only
value the tool needs.

Two things the tool will not do: it will not request `Notes.ReadWrite`,
`Sites.Read.All` or anything broader if `Notes.Read` is refused — it stops and
says so — and it never asks for a password, a cookie, an access token or a
refresh token.

`offline_access` is deliberately not requested, so no refresh token exists and
the tool cannot act unattended later.

## Running the bounded proof

```
python -m tools.onenote_export export \
    --client-id <application-client-id> \
    --out ./onenote-export \
    --max-pages 20 \
    --notebook "Õigusloome"
```

Sign in in a browser with the code it prints. It then walks notebooks, section
groups, sections and pages, stopping at `--max-pages`. Twenty is the authorised
ceiling for a proof; there is no unlimited value.

Verify, on the machine, that the archive contains: the notebook, the section
hierarchy, page metadata, `page.html`, `page.txt`, at least one image, at least
one Office or PDF attachment with a SHA-256, and that a section with more than
twenty pages produced more than twenty (which proves paging worked). Then stop.

## The archive

```
onenote-export/
    manifest.jsonl                 one JSON object per page
    pages/
        {PAGE_ID}/
            metadata.json          the same record, beside its content
            page.html              exactly what Graph returned
            page.txt               derived from it, for reading and matching
            attachments/
                {sha-prefix}-{filename}
```

`page.html` is the source representation; `page.txt` is derived. Keeping only
the cleaned text would be the same mistake as storing extracted text instead of
a PDF.

JSON Lines rather than one document, so an export interrupted after 400 pages
leaves 400 complete records rather than an unparseable array.

## Reconciliation — retired (owner decision D5, 2026-11-04)

The `reconcile` command and `reconcile.py` are removed, and
`python -m tools.onenote_export reconcile` now refuses with exit code 2.

It was unsafe for the register's links:

* It took the first `id`/`page-id` query value or the first braced GUID as the
  page id. On the register's `Doc.aspx` links that is `sourcedoc`, the
  **notebook** GUID, which every link shares, so all links collapsed onto one
  identity. On `OneNote.aspx` links, `id` is a notebook path.
* It treated a shared `YYYY_NNN` token as an automatic match, taking the first
  page that carried it.

A register link now names a page only where it names one: the last GUID of
`wd=target(…)` or a client link's `page-id`
(`app/legacy_import/onenote_capture.register_page_guids`, ADR 0204). The capture
import (ADR 0205) requires the link to name exactly the page the desktop reader
captured; title similarity is never a match. The regression tests are in `tests/test_onenote_export.py`.

## Security notes

The bearer token is sent to exactly two hosts: `graph.microsoft.com` and
`www.onenote.com`. The second is not a workaround — it is where page HTML points
its `src`, `data-fullres-src` and `data` attributes, per current Microsoft
documentation.

Resource URLs come out of page HTML, which is content a person authored and may
have pasted from anywhere. Every one is checked for scheme, host and Graph
resource path shape before a token is attached; anything else is counted as
skipped and never fetched. The test suite includes a page that names an image on
a hostile host and asserts the request is never made.

## Verified against

Microsoft Learn, current as of August 2026:

- *Use the OneNote REST API* — service root `…/me/onenote/{notebooks | sections |
  sectionGroups | pages}`, and that the OneNote API does not support app-only
  authentication.
- *Get OneNote content and structure by using the OneNote API* — page content at
  `…/pages/{id}/content`, resources at `…/resources/{id}/$value`, resource URLs
  in `img[src]`, `img[data-fullres-src]` and `object[data]` with the filename in
  `data-attachment`, `pagelevel=true` for page order, default 20 entries per
  response with `@odata.nextLink`, `$top` maximum 100, and `Notes.Read` among the
  scopes permitting GET.
