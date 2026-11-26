# 0230 — Untrusted files are parsed in a no-network, no-secret container

Date: 2026-11-26 · Status: proposed (SEC-008, security round of 26.11.2026; prepared and tested only — nothing merged, deployed or changed on the host)

## Context

Every parser in `app/documents/extraction/` reads bytes that came from outside:
a file somebody picks on `Uus teema`, a forwarded `.eml` or `.msg`, a PDF a register
published. Underneath are large native and pure-Python code bases — pypdfium2 (PDFium),
Pillow, Tesseract, python-docx, openpyxl, extract-msg, the e-mail parser. ADR 0014's
"a parser is a pure function of bytes" is a statement about our code, not theirs.

Before this record, parsing ran synchronously inside the long-lived process that asked
for it:

* the `intake-reader` container (`run_intake_reader`), which holds the database
  password and the Django secret key in its environment (`env_file`), sits on the
  `internal` bridge with a route to `db` and to the internet, and has no memory limit;
* `web`, for the nightly Riigikogu document reads (`app/riigikogu/documents.py`,
  run under `docker exec --env-file $WATCH_ENV`, which adds the AI keys) and for
  operator corpus runs (`run_extraction_worker`, `extract_pending_documents`).

The parsers' own budgets (ADR 0176, ADR 0182: page, pixel, member, character limits)
bound what a *well-behaved* parse allocates. Nothing bounded wall-clock time, memory,
CPU, child processes or temporary storage of one that was not, and anything that took a
parser over had the credentials and the network of the process it ran in. Codex
Security finding SEC-008 (MEDIUM).

## Decision

1. **One door.** Every parse goes through `app.documents.extraction.isolation`
   (`parse_isolated`), reached from `orchestrator.parse_source` (intake and corpus) and
   from `app/riigikogu/documents.py`. `DOCUMENT_PARSER_ISOLATION` chooses the mode:
   `service`, `subprocess` (default on POSIX) or `inprocess` (the test suite; refused
   by `run_intake_reader` outside DEBUG and the tests).
2. **A child per file.** `isolation.run_child` starts
   `python -B -P -m app.documents.extraction.isolation_child <job-dir>` with: an
   allow-listed environment (`PATH`, `LANG`, `LC_ALL`, `TESSDATA_PREFIX` plus the
   child's own `HOME`/`TMPDIR`/`PYTHONPATH`; never a credential); its own session
   (`start_new_session`); `setrlimit` before `exec` on address space, CPU seconds,
   file size, open files and core size; `oom_score_adj = 1000`; a wall-clock deadline;
   a cap on what it may print; a private `0700` job directory removed afterwards. The
   child configures Django from the `EXTRACTION_*` settings in the request and nothing
   else — no settings module, no database configuration, no secret key. When it
   finishes, overruns or floods, the whole session is `SIGKILL`ed and reaped with
   `wait4`, whose rusage is logged as numbers only.
3. **The boundary is a container.** In production the child runs inside a dedicated
   Compose service, `intake-parser` for the reader and `batch-parser` for `web`, with
   `network_mode: none`, no `env_file`, `read_only: true`, `cap_drop: [ALL]`,
   `no-new-privileges`, `init: true`, a 256 MiB scratch tmpfs as the only writable
   disk, `mem_limit`/`memswap_limit`, `cpus` and `pids_limit`. The caller reaches it
   over a Unix socket on a 1 MiB tmpfs volume shared with exactly one caller. The
   service (`isolation_server`) handles one file at a time, clamps every requested
   limit to its own ceiling, strips test hooks, and sweeps the scratch tmpfs and the
   socket directory after every file.
4. **The answer is data.** The child prints one JSON verdict; the caller validates it
   field by field (`decode_result`), rebuilds the `base.py` dataclasses, refuses a
   verdict for a different parser than the one it chose, and runs
   `guard_parse_result` again on its own side. Nothing is unpickled. An intake request
   asks for text only, so no thumbnail or attachment bytes cross at all.

## Requirements and how each is met

| Requirement | Mechanism | Where enforced |
|---|---|---|
| Max total time per document | wall-clock deadline in `_collect`; whole session `SIGKILL`ed at it; caller socket timeout = deadline + queue allowance + 15 s | child (both modes) and caller |
| Memory limit | `RLIMIT_AS` on the child and everything it starts; container `mem_limit` = `memswap_limit` as the cgroup ceiling; `oom_score_adj` makes the parser the OOM victim, not the service | kernel (rlimit + cgroup) |
| CPU limit | `RLIMIT_CPU` (soft → `SIGXCPU`, hard +5 s → `SIGKILL`); container `cpus: 1.0`; `OMP_THREAD_LIMIT=1` | kernel |
| Bounded child processes | container `pids_limit: 32` (cgroup, counts the whole tree); every descendant in the child's session, killed with it | kernel (cgroup) + `killpg` |
| Bounded temp storage and output | `RLIMIT_FSIZE` per file; 256 MiB scratch tmpfs (charged to the cgroup's memory); 1 MiB socket tmpfs; output cap per budget; read-only root | kernel + reader |
| Safe termination of parser + children | own session; `killpg(SIGKILL)` after every run, success or not; `init: true` reaps orphans; service `SIGTERM` unwinds through the same kill | service + kernel |
| No unnecessary network | `network_mode: none` (loopback only) | Docker |
| No DB credentials / app secrets | no `env_file`, no DB variables; child environment allow-listed; Django configured from `EXTRACTION_*` only; in `subprocess` mode the parent is `PR_SET_DUMPABLE 0`, so `/proc/<ppid>/environ` is unreadable | Docker + code |
| Minimal filesystem | read-only root; mounts are the socket tmpfs and the scratch tmpfs only — not the staging volume, not evidence; the caller sends the one claimed file's bytes | Docker |

## Limits

Defaults (`config/settings.py`, ceilings in `deploy/unraid-main/compose.yml`):

| Limit | Intake | Batch | Container ceiling |
|---|---|---|---|
| Wall clock per file | 120 s | 1200 s | 1500 s |
| CPU seconds per file | 120 s | 1200 s | 1500 s; `cpus: 1.0` |
| Address space (`RLIMIT_AS`) | 2 GiB | 2 GiB | 2 GiB; `mem_limit` 2560 MiB |
| Single file written (`RLIMIT_FSIZE`) | 256 MiB | 256 MiB | 256 MiB; scratch tmpfs 256 MiB |
| Output (JSON) | 64 MiB | 512 MiB | 512 MiB |
| Input | — | — | 128 MiB (upload ceiling is 100 MiB) |
| Processes | — | — | `pids_limit: 32` |
| Queue wait at the service | 120 s | 120 s | — |

The intake budget is held inside the reader's stale-claim window by system check
`documents.E001` (deadline + queue + 15 s < `INTAKE_READER_STALE_CLAIM_MINUTES`), so a
file the boundary stops is settled by the pass that claimed it, never reclaimed behind
its back; the batch budget likewise inside `EXTRACTION_STALE_CLAIM_MINUTES`.

**Measured consumption** (Linux CI, `tests/test_parser_isolation.py::
test_sizing_measurements_per_fixture_class`, synthetic fixtures from
`tests/synthetic_corpus.py`; one child each; peak RSS from `wait4`, peak address space
from the child's own `VmPeak`) is recorded in the SEC-008 report of 26.11.2026 and in
the CI log of the pull request that introduced this record. Every class — plain text,
ordinary and 400-page PDFs, scanned PDF and PNG through Tesseract, DOCX, XLSX, EML and
MSG with attachments, a malformed PDF and EML, a ZIP bomb presented as DOCX — finishes
far inside every limit; the test fails if one does not.

**What the limits affect.** A file somebody adds on `Uus teema` that takes longer than
120 s to read — in practice a long scan that needs OCR page by page — gets no
suggestions and reads «Faili lugemine katkestati, sest see kestis üle 120 sekundi.
Originaal on alles ja avatav.»; before this record the same file outlived the reader's
five-minute claim and was reclaimed until its attempts ran out. The file itself is
stored and opens as always, and the Teema is created regardless. A batch file past
1200 s (a several-hundred-page scan) fails the same way in a corpus run and can be
re-run with a larger `DOCUMENT_PARSER_BATCH_WALL_SECONDS` by an operator who decides to.

**Inside the nightly Riigikogu step** (r96 integration with docs/adr/0231). The step
has a 98-minute budget around its own 90-minute limit, so a 1200 s parse near its
end would carry it past the budget and be killed by the supervisor. `parse_isolated`
takes ``wall_seconds``, which only narrows the budget's wall (and CPU) for one file,
and `app/riigikogu/documents.py` passes the stage's time left (`client.time_left`)
less 30 s. With no time left the file is not parsed; a parse stopped by that narrowed
wall is `client.OutOfTime` (the stage's «the rest next night»), never an unreadable
letter. A file stopped by the plain 1200 s budget stays the file's own failure.
Tests: `tests/test_r96_riigikogu_parse_deadline.py`. The supervisor stopping the
step ends the socket client in `web`; `batch-parser` finishes or stops the one file
in hand at its own wall and sweeps it, and the next night's request queues behind it
for at most `DOCUMENT_PARSER_QUEUE_SECONDS`.

## Failure

* **The boundary stopped the file** (deadline, CPU, memory, file size, output, crash,
  malformed answer): the outcome is FAILED with the boundary's code
  (`parser_timeout`, `parser_cpu_limit`, `parser_memory_limit`, `parser_file_limit`,
  `parser_output_limit`, `parser_crashed`, `parser_protocol`) and a sentence that says
  the original is kept. Settled through the ordinary fenced write, once; it is not
  offered again, so there is no retry loop. The queue moves to the next file.
* **The boundary could not be reached** (`ParserUnavailable`: the service restarting,
  its socket missing): not a verdict on the file. The claim is handed back through a
  fenced update (PENDING, attempts untouched), the batch stops, and
  `run_intake_reader` waits 15 s before looking again; the corpus commands stop and
  say so. A pass whose claim was taken meanwhile writes nothing (CLAIM_LOST).
* **A stale worker** cannot overwrite a newer result: every write, the hand-back
  included, is conditional on the claim this pass took (ADR 0072, unchanged).
* **Teema creation never waits on parsing** (ADR 0072): a file still PENDING because
  the parser is down is promoted as PENDING with the Teema.

## Gaps

Named, not hidden:

1. **`subprocess` mode is defence in depth, not the boundary.** An unprivileged process
   under Docker's default seccomp profile cannot unshare a network namespace or remount
   its filesystem, so a child in `subprocess` mode still has the container's network and
   can read what the container can read (in `web`: evidence, read-write). It has no
   secret in its environment and cannot read the parent's. Production does not use it:
   `web` and `intake-reader` are configured for `service`. It remains the default where
   nothing is configured (a developer's Linux shell, the `unraid-test` stack, a
   rehearsal), which is strictly better than the in-process parsing it replaces.
2. **Within one file, a child can touch the socket directory.** It runs as the same
   user as the service, so it could unlink or replace the socket or leave files beside
   it. Bounded: the directory is a 1 MiB tmpfs; the service sweeps it and rebinds after
   every file and every five idle seconds; the child is dead before the next file is
   accepted. The worst case is a missed intake read until the reader's next turn.
3. **One file at a time per service.** A long batch file never delays an intake file
   (separate services), but two intake files from two forms queue; the queue allowance
   (120 s) is inside the stale-claim window by the system check.
4. **The legacy archive import** (`app/legacy_import/opinion_text.py`) still calls
   `PdfParser` in its own process. It is an operator command over Koda's own historical
   archive, not a reader of outside files, and was left as it is.
5. **Limits are sized on synthetic fixtures.** Real documents may be larger; the
   numbers above leave a wide margin, and every stop is logged with its usage so the
   limits can be tuned from what production actually sees.

## Alternatives considered

* **A sanitized-environment subprocess alone.** Removes the secrets and bounds the
  resources, but keeps the network and the filesystem (Gap 1). Not sufficient on its
  own by the brief's own bar; kept as the in-container layer.
* **Namespaces / seccomp / Landlock inside the reader container.** Needs
  `CAP_SYS_ADMIN` or unprivileged user namespaces, which Docker's default seccomp
  profile blocks; adding capabilities to a container that holds credentials goes the
  wrong way.
* **gVisor / a VM per file.** Strongest, but a new runtime on the Unraid host; not the
  smallest architecture that meets the requirements.
* **A file-spool queue between containers.** Same isolation, more moving parts (two
  directories, polling, orphan cleanup) than one socket and one request.

## Consequences

Two new containers on the release image, each ≤ 2.5 GiB of memory under load and idle
otherwise; one socket volume each; no migration; no new data, no backup change. A
release replaces them like every worker (`assert_deployment_identity` derives them).
Rollback is the previous image: older code ignores the new settings and parses in
process, and the two containers can be left running or removed. Operator procedure:
`deploy/unraid-main/PARSER-ISOLATION.md`.
