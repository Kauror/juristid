"""Command line for the OneNote proof of concept.

    python -m tools.onenote_export export --client-id <id> --out ./onenote-export

The ``reconcile`` command is retired (owner decision D5, 2026-11-04): it took a
notebook GUID or notebook path for a page id and collapsed register links onto
one identity. A register link's page is read by
``app.legacy_import.onenote_capture.register_page_guids`` (ADR 0204, 0205).

Runs outside Django. It does not import the application, does not read its
settings, and does not need a database — so it can be run on a Koda-controlled
workstation that has the notebook, without that machine also being a Juristid
deployment (Stage-2B brief 51, 60).

What it prints is counts. Page titles, URLs, filenames and company names stay in
the output directory on the machine that produced them; the summary is the part
that may be pasted into a report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RETIRED = (
    "reconcile is retired (owner decision D5, 2026-11-04): it mistook notebook ids for page ids. "
    "A register link's page is read by app.legacy_import.onenote_capture.register_page_guids "
    "(ADR 0204, 0205)."
)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["reconcile"]:
        print(RETIRED, file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="tools.onenote_export", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="Read notebooks and write a neutral archive.")
    export.add_argument("--client-id", required=True, help="Entra application (client) id.")
    export.add_argument("--tenant", default="organizations")
    export.add_argument("--out", required=True, type=Path)
    export.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Hard ceiling on pages exported. Stage 2B authorises a bounded proof, "
        "not a notebook download.",
    )
    export.add_argument("--notebook", default="", help="Only notebooks whose name contains this.")

    args = parser.parse_args(argv)
    return _export(args)


def _export(args: argparse.Namespace) -> int:
    from tools.onenote_export.auth import ConsentRequired, begin_device_code, poll_for_token
    from tools.onenote_export.export import export_to
    from tools.onenote_export.graph import GraphClient

    prompt, device_code = begin_device_code(client_id=args.client_id, tenant=args.tenant)
    print("\nSign in to approve read-only OneNote access:\n")
    print(f"  1. Open {prompt.verification_uri}")
    print(f"  2. Enter the code {prompt.user_code}")
    print("  3. Approve the Notes.Read permission.\n")
    print("Waiting…", flush=True)

    try:
        token = poll_for_token(
            client_id=args.client_id, device_code=device_code, tenant=args.tenant
        )
    except ConsentRequired as error:
        # Stop here rather than asking for something broader. A tool that
        # escalates its own request until one is granted is how an application
        # ends up holding write access nobody meant to give it.
        print(f"\nStopped: {error}", file=sys.stderr)
        return 2

    summary = export_to(
        args.out,
        GraphClient(token.value),
        max_pages=args.max_pages,
        notebook_filter=args.notebook,
    )
    print(f"\nArchive written to {args.out}\n")
    print(summary.as_text())
    if summary.errors:
        print("\nErrors (types only; no content):")
        for error in summary.errors[:20]:
            print(f"  {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
