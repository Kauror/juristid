"""What must be true before a plan may claim the Chamber sent a letter.

``apply_plan`` already refused a plan built against different *sources*
(``require_unchanged_sources``). That is necessary and it is not sufficient: the
sources can be identical while the plan is not, because the plan is also built
from the database — the review queue, existing Submissions, the candidates a
person decided between the review and the apply. A reviewer who read a plan on
Monday and applied it on Friday could be applying a different set of facts under
the same source digests.

Two things live here, and they answer two different questions.

**The plan digest** answers *is this the plan somebody read?* It is computed
over the apply-relevant facts only — the identity of each proposed Submission,
the Matter it lands on, its date and the basis for that date, its recipient
string and the basis for that, the candidate it came from — and deliberately not
over counts, warnings, orderings, file paths or anything else that can move
without changing what would be written. A digest that changed when a warning was
reworded would be a digest nobody could use.

**The conflict scan** answers *would writing this contradict something already
recorded?* It is run on the reviewed route and the automatic route alike, which
is the point: the automatic route grew its protections first, and a reviewed
decision is not a reason to skip them. A person approving a letter is asserting
whose it is, not asserting that no one else has filed it since.

Nothing here writes. Both are called before the transaction that does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.legacy_import.opinion_enums import OpinionCandidateState

#: Bumped when the *contents* of the digest change — a field added, removed or
#: normalised differently. Recorded beside the digest so a stored approval can
#: always be read back under the rules that produced it, and so a reviewer is
#: never silently compared against a digest computed by different code.
APPLY_PLAN_DIGEST_VERSION = "1.0"


class PlanChanged(Exception):
    """The plan is not the one that was approved."""


class ApplyConflict(Exception):
    """Writing this plan would contradict something already recorded."""


def plan_digest(plan: Any) -> str:
    """A digest over exactly what an apply would write.

    Sorted by the Submission's own identity rather than by plan order, so a
    planner that emits the reviewed route before the automatic one — or stops
    doing so — does not change the digest of an unchanged decision.

    ``matter_id`` and ``candidate_id`` are stringified because they are UUIDs
    whose JSON form would otherwise depend on the driver.
    """
    rows = sorted(
        (
            {
                "sha256": item.sha256,
                "matter": str(item.matter_id),
                "kind": item.kind,
                "sent_date": item.sent_date.isoformat() if item.sent_date else "",
                "sent_date_basis": item.sent_date_basis,
                "recipient_raw": item.recipient_raw.strip(),
                "recipient_basis": item.recipient_basis,
                "match_class": item.match_class,
                "candidate": str(item.candidate_id) if item.candidate_id else "",
                "existing_submission": (
                    str(item.existing_submission_id) if item.existing_submission_id else ""
                ),
            }
            for item in plan.submissions
        ),
        key=lambda row: (row["sha256"], row["matter"], row["candidate"]),
    )
    payload = json.dumps(
        {"version": APPLY_PLAN_DIGEST_VERSION, "submissions": rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_reviewed_plan(plan: Any, expected: str) -> str:
    """Refuse unless the plan hashes to exactly the digest somebody approved.

    Recomputed here, immediately before the apply, rather than trusted from the
    operator's earlier run. "Rebuild the current plan and apply whatever it now
    says" is the failure this exists to prevent, and it is indistinguishable
    from the safe case unless the comparison happens at apply time.
    """
    digest = plan_digest(plan)
    expected = (expected or "").strip().lower()
    if not expected:
        raise PlanChanged(
            "Kanooniline arvamuste rakendamine nõuab ülevaadatud plaani räsi "
            "(--expect-plan-sha256). Praeguse plaani räsi on " + digest + "."
        )
    if digest != expected:
        raise PlanChanged(
            "Plaan on muutunud pärast ülevaatust. "
            f"Ülevaadatud {expected[:16]}…, praegune {digest[:16]}…. "
            "Ehita plaan uuesti ja vaata see üle."
        )
    return digest


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conflict:
    """One reason a proposed Submission may not be written."""

    sha256: str
    reason: str
    detail: str


def scan_conflicts(plan: Any) -> list[Conflict]:
    """Everything already recorded that this plan would contradict.

    Returned rather than raised, so an operator sees the whole list instead of
    the first one and fixes them in one pass. ``require_no_conflicts`` is what
    stops the apply.

    Four questions, and they are genuinely different records:

    * has this occurrence already produced a canonical Submission?
    * do these exact bytes already stand as evidence on a *different* Matter?
    * has the candidate behind this plan already been applied?
    * has somebody already decided the candidate the other way?

    The second is the one the reviewed route never asked. Two reviewers filing
    the same letter onto two Matters is not a duplicate row that a unique
    constraint would catch — it is two different true-looking claims about one
    dispatch.
    """
    from app.legacy_import.opinion_archive import (
        OpinionArchiveItem,
        OpinionMatchCandidate,
        OpinionSubmissionImport,
    )
    from app.submissions.models import Submission

    conflicts: list[Conflict] = []
    shas = [item.sha256 for item in plan.submissions]
    if not shas:
        return conflicts

    imported = {
        row.item.sha256: row
        for row in OpinionSubmissionImport.objects.select_related("item", "submission").filter(
            item__sha256__in=shas
        )
    }
    items_by_sha: dict[str, Any] = {}
    for item in OpinionArchiveItem.objects.filter(sha256__in=shas):
        items_by_sha.setdefault(item.sha256, item)

    # Which Matter already holds these exact bytes as a submission's evidence.
    bound: dict[str, set[Any]] = {}
    for submission in Submission.objects.filter(final_version__sha256__in=shas).select_related(
        "final_version"
    ):
        version = submission.final_version
        if version is None:  # pragma: no cover - excluded by the filter
            continue
        bound.setdefault(version.sha256, set()).add(submission.matter_id)

    candidate_states = dict(
        OpinionMatchCandidate.objects.filter(
            pk__in=[item.candidate_id for item in plan.submissions if item.candidate_id]
        ).values_list("pk", "state")
    )

    for entry in plan.submissions:
        existing = imported.get(entry.sha256)
        if existing is not None and existing.submission.matter_id != entry.matter_id:
            conflicts.append(
                Conflict(
                    sha256=entry.sha256,
                    reason="ALREADY_IMPORTED_ELSEWHERE",
                    detail=(
                        "See kiri on juba kanooniliselt seotud teise teemaga "
                        f"({existing.submission.matter_id})."
                    ),
                )
            )
            continue

        others = bound.get(entry.sha256, set()) - {entry.matter_id}
        if others:
            conflicts.append(
                Conflict(
                    sha256=entry.sha256,
                    reason="EVIDENCE_BOUND_ELSEWHERE",
                    detail=(
                        "Samad baidid on juba mõne teise teema arvamuse lõplik tõend "
                        f"({len(others)} teemal)."
                    ),
                )
            )
            continue

        state = candidate_states.get(entry.candidate_id) if entry.candidate_id else None
        if state == OpinionCandidateState.APPLIED and existing is None:
            # The candidate says the work is finished and no provenance row
            # exists to show for it. That is a broken pair, not a green light.
            conflicts.append(
                Conflict(
                    sha256=entry.sha256,
                    reason="CANDIDATE_APPLIED_WITHOUT_PROVENANCE",
                    detail="Kandidaat on märgitud rakendatuks, kuid päritolukirjet ei ole.",
                )
            )
        elif state == OpinionCandidateState.REJECTED:
            conflicts.append(
                Conflict(
                    sha256=entry.sha256,
                    reason="CANDIDATE_REJECTED",
                    detail="Ülevaataja on selle kandidaadi tagasi lükanud.",
                )
            )

    return conflicts


def require_no_conflicts(plan: Any) -> None:
    """Raise on the whole set, so one run reports every blocker it found."""
    conflicts = scan_conflicts(plan)
    if not conflicts:
        return
    lines = "\n".join(
        f"  {conflict.sha256[:16]}…  {conflict.reason}  {conflict.detail}" for conflict in conflicts
    )
    raise ApplyConflict(f"{len(conflicts)} vastuolu takistab rakendamist:\n{lines}")
