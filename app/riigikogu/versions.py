"""What changed between two versions of a bill's text — established without a model (ADR 0227 §4).

Step 1 is **which documents to compare**: a bill's ``texts[]`` mixes the draft,
its explanatory memorandum, annexes, signed containers and the second reading's
list of amendments. `document_role` names each by its file name and title, and
only two documents of the role «eelnõu tekst» are ever compared.

Step 2 is a **structural comparison**: the text is cut into provisions
(«§ 3.», a numbered point «12)», a subsection «(2)») and the provisions are
matched by their label, so a renumbering is not reported as a rewrite of every
section after it when the wording moved unchanged. What a model may later do is
*explain* the changes found here; it never decides whether something changed.
Where the cutting is unreliable (no § found in either text) the comparison says so.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

ROLE_DRAFT = "eelnõu tekst"
ROLE_MEMO = "seletuskiri"
ROLE_AMENDMENTS = "muudatusettepanekute loetelu"
ROLE_ANNEX = "lisa"
ROLE_CONTAINER = "allkirjastatud konteiner"
ROLE_OTHER = "muu"

_SECTION = re.compile(r"(?m)^\s*(§\s*\d+[¹²³⁴⁵⁶⁷⁸⁹\d]*\.)")


def document_role(file_name: str, title: str = "") -> str:
    """What a bill text document is, from its name. Unknown is «muu», never «eelnõu tekst»."""
    name = f"{file_name} {title}".casefold()
    if name.endswith((".asice", ".bdoc", ".ddoc")) or ".asice" in name:
        return ROLE_CONTAINER
    if "mede_loetelu" in name or "muudatusettepanekute loetelu" in name:
        return ROLE_AMENDMENTS
    if "lisa" in name and ("sk_lisa" in name or "seletuskirja_lisa" in name or "lisa_" in name):
        return ROLE_ANNEX
    if "seletuskiri" in name or "_sk." in name or "_sk_" in name or name.endswith("sk.docx"):
        return ROLE_MEMO
    if "eelnõu" in name or "eelnou" in name or "seaduseelnõu" in name:
        return ROLE_DRAFT
    return ROLE_OTHER


@dataclass(frozen=True)
class Change:
    provision: str
    #: ``added``, ``removed``, ``changed`` or ``moved``.
    kind: str
    before: str
    after: str


@dataclass(frozen=True)
class Comparison:
    reliable: bool
    changes: tuple[Change, ...]
    note: str = ""


def _provisions(text: str) -> list[tuple[str, str]]:
    """``[(«§ 3.», body), …]`` in order; a text without any § is one unlabelled piece."""
    parts = _SECTION.split(text or "")
    if len(parts) < 3:
        return []
    found = []
    for index in range(1, len(parts) - 1, 2):
        label = " ".join(parts[index].split())
        found.append((label, " ".join(parts[index + 1].split())))
    return found


def compare(old: str, new: str) -> Comparison:
    before, after = _provisions(old), _provisions(new)
    if not before or not after:
        return Comparison(
            False, (), "Teksti ei õnnestunud sätete kaupa jaotada; võrdlus pole usaldusväärne."
        )
    old_map, new_map = dict(before), dict(after)
    changes: list[Change] = []
    old_bodies = {body: label for label, body in before}
    for label, body in after:
        if label not in old_map:
            moved_from = old_bodies.get(body)
            if moved_from:
                changes.append(Change(label, "moved", moved_from, label))
            else:
                changes.append(Change(label, "added", "", body))
        elif old_map[label] != body:
            if _formatting_only(old_map[label], body):
                continue
            changes.append(Change(label, "changed", old_map[label], body))
    new_bodies = set(new_map.values())
    for label, body in before:
        if label not in new_map and body not in new_bodies:
            changes.append(Change(label, "removed", body, ""))
    return Comparison(True, tuple(changes))


def _formatting_only(one: str, other: str) -> bool:
    strip = re.compile(r"[\s„“”\"«»]")
    return strip.sub("", one) == strip.sub("", other)


def word_diff(before: str, after: str) -> list[tuple[str, str]]:
    """``[(op, words)]`` with op ``=``, ``-`` or ``+``: what a reader sees highlighted."""
    a, b = before.split(), after.split()
    out: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(("=", " ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(("-", " ".join(a[i1:i2])))
        if op in ("insert", "replace"):
            out.append(("+", " ".join(b[j1:j2])))
    return out


def explain(*, bill: Any, old: Any, new: Any, model: Any = None) -> Any:
    """The deterministic comparison, with the model's explanations attached to it.

    The model is told which provisions changed and asked only to explain them; an
    explanation for a provision the comparison did not find is dropped, so the
    model cannot add a change (ADR 0227 §4).
    """
    from app.riigikogu import analysis
    from app.riigikogu.enums import AnalysisType

    found = compare(old.text, new.text)
    listed = "\n".join(f"- {c.provision}: {c.kind}" for c in found.changes)
    row = analysis.run(
        AnalysisType.VERSION_CHANGES,
        [old, new],
        bill=bill,
        context=(
            "The provisions below changed between the first and the second source "
            "(established by comparing the texts). Explain each one in Estonian: what "
            "changed in substance and why it may matter to businesses. Mark uncertainty.\n" + listed
        ),
        model=model,
    )
    if row.status == "DONE" and "reliable" not in row.result:
        notes = {
            c.get("provision"): c for c in row.result.get("changes", []) if isinstance(c, dict)
        }
        row.result = {
            "reliable": found.reliable,
            "note": found.note,
            "from_label": getattr(old, "label", ""),
            "to_label": getattr(new, "label", ""),
            "changes": [
                {
                    "provision": c.provision,
                    "kind": c.kind,
                    "before": c.before,
                    "after": c.after,
                    "explanation": (notes.get(c.provision) or {}).get("explanation", ""),
                    "relevance": (notes.get(c.provision) or {}).get("relevance", ""),
                    "uncertain": bool((notes.get(c.provision) or {}).get("uncertain")),
                }
                for c in found.changes
            ],
            "dropped": [p for p in notes if p not in {c.provision for c in found.changes}],
        }
        row.save(update_fields=["result", "updated_at"])
    return row
