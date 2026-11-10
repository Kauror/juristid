# OneNote desktop capture — operator runbook

The desktop OneNote client is the only reader (docs/adr/0204). A capture is
collected by hand on an approved workstation, sealed there, moved to the host's
read-only source corpus, verified on the host and imported through the gated
importer (docs/adr/0205 and its amendments). The web application never drives
OneNote: «Andmete sünkroonimine» only *requests* a verification or an import of
a capture that is already on the host.

Nothing here writes to OneNote, SharePoint, OneDrive or Lovable. No Microsoft
Graph, no browser automation, no sign-in on the operator's behalf.

**Never commit or paste** a request file, a run directory, a sealed capture, a
`--out` report, a page title, a filename or any page text. Only counts and
reason codes may leave the workstation in a message.

## What you need

| | |
| --- | --- |
| Workstation | the approved one, OneNote desktop signed in to the department notebook |
| PowerShell | Windows PowerShell 5.1 (`capture_pages.ps1` is ASCII-only for it) |
| Python | 3.13 and a checkout of this repository (only `tools/onenote_capture` is used) |
| Disk | a capture directory outside every Git checkout and every synced folder |

## 1. On the host — what to capture

The worklist is never the whole notebook: operational Teemad whose register
link names a page that has no capture, pages of Teemad whose Lovable record or
OneNote link changed since the last capture, a link Lovable now names that the
register has not accepted, and pages a mark sends back (`FAILED`,
`NEW_MATERIAL`, `NOT_INSPECTED`).

```bash
IMAGE="$(docker inspect -f '{{.Image}}' juristid-main-web)"
ENV=/mnt/user/appdata/juristid-main/config/juristid.env
run() { docker run --rm --network juristid-main-internal --env-file "$ENV" \
  --log-driver none -v /mnt/user/appdata/juristid-main/onenote:/out \
  --entrypoint python "$IMAGE" manage.py onenote_capture_import "$@"; }
run coverage                                  # counts only
run worklist --out /out/request-$(date -u +%Y%m%d).json
```

`worklist --since 2026-11-04T00:00Z` widens the window; without it, changes after
the latest capture count. The request file holds identities only (page GUIDs,
Teema and Lovable ids, references) — no titles or text. Move it to the
workstation by the usual private channel.

For the **section-by-section** historical process (owner brief §10) there is no
request page list: capture one named section (step 3, `-Section`) after the
operational coverage is finished. The request file is still needed: its
`register_map` is how the sealer knows which pages the register names exactly.

## 2. On the workstation — before you start

1. Open OneNote. Wait until the notebook shows as **synced** (no pending sync
   icon); the owner confirms "OneNote open and synced". The laptop's replica is
   the source; nothing checks the server's time (ADR 0204 §6, §8.2).
2. Do not edit pages while the capture runs. The capture opens pages
   (`NavigateTo`) only when placeholders remain; you will see OneNote move.
3. Note the page you are on: the script cannot read it through PowerShell
   (`run-log.json` says `restore_unavailable`), so after a run that navigated
   OneNote stays on the last captured page. Go back by hand.

## 3. Capture only those pages

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\onenote_capture\capture_pages.ps1 `
  -Request D:\capture\request-YYYYMMDD.json -Notebook "<notebook name>" `
  -OutDir D:\capture\run-YYYYMMDD-1
# or one section:
#   ... -Section "<section name>" -Notebook "<notebook name>" -OutDir D:\capture\run-...
```

What it does, read-only:

* `GetHierarchy` before and after (the verifier compares them);
* `GetHyperlinkToObject` per page, to read the desktop page GUID — the identity
  that survives renames, moves and archiving (D2);
* `GetPageContent` before navigation (the dates come from this read);
* `NavigateTo` the page and each placeholder **only** while an empty block or an
  uncached file remains, then polls until **0 placeholders and every file
  cached** — "stable for two polls" is not completeness;
* copies every file from OneNote's cache, hashing source, copy and source again;
* retries only OneNote's two "busy" errors (`RPC_E_CALL_REJECTED`,
  `RPC_E_SERVERCALL_RETRYLATER`), with back-off.

`missing.tsv` lists requested GUIDs the hierarchy does not hold (deleted, moved
to another notebook, no access). Report them; on the host they are marked
`FAILED` (step 8).

## 4. Check the run — STOP means a person decides

```powershell
python -m tools.onenote_capture.verify raw D:\capture\run-YYYYMMDD-1
```

Exit 2 (STOP) on: blocks lost, added, reordered or re-texted between the two
reads; a creation time changed; a modification time changed other than the
known navigation touch; a page binding broken; a page or its section modified in
the hierarchy during the run; placeholders left; a file not fetched or whose
copy differs. **Do not seal a STOP run.** For placeholders, capture just those
pages again into a new, empty run directory.

## 5. Seal

```powershell
python -m tools.onenote_capture.seal D:\capture\run-YYYYMMDD-1 `
  D:\capture\request-YYYYMMDD.json D:\capture\sealed\onenote-desktop-YYYYMMDD-1 `
  onenote-desktop-YYYYMMDD-1
python -m tools.onenote_capture.verify sealed D:\capture\sealed\onenote-desktop-YYYYMMDD-1
```

The sealer refuses (and writes nothing) for an unbound page, a page with
placeholders, a file not verified, bytes that differ from the receipt, or an
existing target. A page the register does not name is sealed with no Teemad: it
is inventoried and never written.

## 6. Move it to the host

Copy the whole sealed directory to the host's read-only source corpus (the
capture directory under `HISTORICAL_SOURCE_ROOT`) by the usual private channel.
Never into Git, CI, a ticket or a chat. The importer refuses a changed, missing
or extra file.

## 7. On the host — verify, then import

In Haldus → «Andmete sünkroonimine» → OneNote:

1. **«OneNote'i impordi kontroll»** requests a verification
   (`onenote_capture.verify_capture`): the whole seal re-hashed, the plan made
   against Juristid, what is already held read back. It shows the plan's counts
   (new pages, new files, new versions, held items, changed pages) and the plan
   digest. Pages get a `VERIFIED` / `NO_CHANGES` / `FAILED` mark.
2. **«Impordi OneNote'i materjalid»** with that digest requests the import
   (`onenote_capture.apply_capture`). The host runner takes the backup first and
   names it (`JURISTID_ONENOTE_BACKUP_SET`); the one-off container carries
   `JURISTID_ONENOTE_CAPTURE_IMPORT=1` and `REAL_DATA_ALLOWED=1`. A plan that
   changed since the verification is refused, never applied from an old preview.

The same by hand, on the one-off container:

```bash
run check --capture /source/capture/onenote-desktop-YYYYMMDD-1 --record --out /out/check.json
run apply --capture ... --expect-manifest-sha256 M --expect-plan-sha256 P \
  --operator-intent onenote-capture-import --backup-set SET --label L --pages 1000
run apply ... --include-shared --pages 1000
run verify --capture ... --require-complete --bytes --out /out/verify.json
```

What an import writes — and what it never does:

* a new page → a page row and its exact-GUID link; a new file on a single-Teema
  page → a Document on its OneNote day;
* other bytes under the same block → the **next version** of the same Document;
  the earlier version, its bytes and hash stay exactly as they were;
* a changed page text, title or place → a new reading of the page beside the
  first one;
* a changed comment or block time, a file on a shared page, a moved-and-edited
  or ambiguous file → recorded and held for a person, never attributed to every
  Teema of a shared page;
* a file gone from the page → counted; nothing is deleted;
* the same capture applied again → nothing.

## 8. Afterwards

```bash
run coverage
run mark --page <GUID> --state FAILED --reason "leht ei avanenud"     # per missing page
```

Coverage counts operational pages only. It never says the archive is complete:
Juristid does not hold the notebook's page or section list. The historical
remainder is processed one section at a time:

```bash
run section-plan --capture /source/capture/<sealed> --section "<name>" --record --out /out/section.json
```

It matches pages only by the register's exact page GUID or a page Juristid
already holds, lists containers, indexes and shared dossiers as what they are,
creates no Teema and invents no register number; every other page is an
exception for a person (`--out`). Its writes are the ordinary gated import.

## Traps

* **PS 5.1 encoding.** A BOM-less `.ps1` is read as ANSI; keep scripts ASCII or
  save with a BOM.
* **Navigation touches** text blocks' `lastModifiedTime`; dates come from the
  pre-navigation read only. The page XML's root `lastModifiedTime` is never a
  page time.
* **Floating files** (on the page canvas) are invisible before the page is
  opened and have no creation time: they stay undated (D8).
* **A move re-issues every block ID** but keeps block times; the importer
  continues a file only by a unique (creation time, filename) with equal bytes.
* **A copied page** has a new GUID and the old times: a new page, its repeated
  files held.
* **An edited attachment** keeps its block and creation time, changes its
  modification time and bytes: the next version.
* **A notebook name with non-ASCII letters** (õ, ä): call the script from a
  PowerShell prompt (`-Notebook "…"` as typed there). Passed through another
  shell's command line it may arrive re-encoded and match no notebook.
* **Archiving by copy-and-delete breaks the register link.** The desktop
  rehearsal of 2026-11-09 found register pages whose GUID sits in *Deleted Pages*
  while a page with the same creation time and title lives in ARHIIV under a new
  GUID (a copy, ADR 0204 D2). `missing.tsv` lists such GUIDs. Nothing is written
  to Lovable or OneNote: Lovable's link stays as provenance, and a person who
  verifies the right page records a reviewed local link in Juristid
  (`MatterSourcePage`, `REVIEWED_MATCH`), which the sync never removes. Never
  by matching titles alone.
* **The rehearsal (2026-11-09, one ARHIIV page, two files):** request mode over
  the department notebook took ~10 s; no navigation was needed; the run
  verified, sealed and compared equal to the 04.11 capture (same page GUID, page
  object ID, 10 blocks in order, two files with identical SHA-256, names and block
  times; only the page XML's root `lastModifiedTime` differed — the 04.11 run's
  own navigation touch). Planned against Juristid it was `NO_CHANGES` and its
  apply wrote nothing.
