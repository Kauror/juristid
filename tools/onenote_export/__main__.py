"""Command line for the OneNote proof of concept.

    python -m tools.onenote_export export --client-id <id> --out ./onenote-export
    python -m tools.onenote_export reconcile --archive ./onenote-export --references rows.json

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
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
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

    reconcile = commands.add_parser(
        "reconcile", help="Compare register links to an export and rank the candidates."
    )
    reconcile.add_argument("--archive", required=True, type=Path)
    reconcile.add_argument(
        "--references",
        required=True,
        type=Path,
        help="JSON list of {id, onenote_url, onenote_page_id, source_title}.",
    )
    reconcile.add_argument("--mapping", type=Path, help="Reviewed {reference_id: page_id} JSON.")
    reconcile.add_argument("--out", type=Path, help="Where to write the candidate list.")

    args = parser.parse_args(argv)
    if args.command == "export":
        return _export(args)
    return _reconcile(args)


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


def _reconcile(args: argparse.Namespace) -> int:
    from tools.onenote_export.archive import Archive
    from tools.onenote_export.reconcile import build_candidates, summarise

    references = json.loads(args.references.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8")) if args.mapping else {}
    manifest = Archive(args.archive).read_manifest()

    candidates = build_candidates(
        source_references=references, manifest=manifest, reviewed_mapping=mapping
    )
    counts = summarise(candidates, total_references=len(references))

    if args.out:
        args.out.write_text(
            json.dumps(
                [candidate.__dict__ for candidate in candidates], ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"Candidates written to {args.out}")

    print("\nReconciliation summary (counts only):")
    for key, value in counts.items():
        print(f"  {key:<20} {value}")
    print(
        "\nNothing has been applied. Tier TITLE_SIMILARITY is a review queue, "
        "never an automatic match."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
