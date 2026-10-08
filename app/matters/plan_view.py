"""`Järgmisena?` as the Teema page reads it — one read, decided in Python.

`Tööplaan` is not drawn any more (docs/adr/0141). What the page still shows is
one suggestion under `PRAEGUNE TEGEVUS`, and only while **this reader** has no
current step: the first step of the background sequence that is still ahead —
suggested or planned, not completed, not dismissed, and **not already done on
the record** (docs/adr/0144 §2). The template must not query and must not
decide.

**Current-ness is the canonical action's, never the step's.** A step is current
exactly when the open `NextAction` names it, so the suggestion and
`PRAEGUNE TEGEVUS` cannot disagree (docs/adr/0133 §3).

**Done on the record.** A suggested step completes only through the action that
was started from it, so a lawyer who published the overview, started the
consultation or sent the opinion through `LISA TEEMALE` used to be offered the
same work again. A standard step now reads as done when the Matter's own
canonical records prove it — keyed on the step's stable `template_step_key` and
`operation`, never on its words:

* `read-material` — anything that could only follow reading it: a published
  `Ülevaade / uudis`, a `Kaasamine`, a sent `Koja arvamus`;
* `website-overview` — a published `Ülevaade`;
* `consult-members` — a `Kaasamine`, waiting or finished;
* `form-position` — feedback recorded on a finished `Kaasamine`, or a sent
  opinion that answers what is being asked;
* `send-opinion` — a sent opinion, **unless a request for an opinion is still
  outstanding**: a repeat `Arvamuse tähtaeg` asks again, and an earlier opinion
  never answers it (docs/adr/0144 §2).

Nothing is written. A step that reads as done is skipped for this page only;
the plan's own rows are untouched, so a dismissal (`×`) still persists exactly
as it did, and a step the record no longer proves comes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.matters.enums import RecordMode, WebsiteOverviewKind, WebsiteOverviewStatus
from app.workflow.enums import PlanStepOperation
from app.workflow.models import MatterPlanStep
from app.workflow.plan import plan_revision, plan_steps_of


@dataclass(frozen=True)
class Recommendation:
    """The one suggested next step, and the revision `×` must name."""

    step: MatterPlanStep
    revision: str


@dataclass(frozen=True)
class RecordedWork:
    """What the Matter's canonical records prove was done, for one reader."""

    published_overview: bool
    published_any: bool
    engagement: bool
    feedback_recorded: bool
    opinion_sent: bool
    opinion_owed: bool

    @classmethod
    def of(cls, matter: Any, viewer: Any) -> RecordedWork:
        """One read per fact, each through the record's own `visible_to`.

        A record restricted below its Matter must not change, by suppressing a
        suggestion, what a reader who may not see it is shown (AUTH-003).
        """
        from app.matters.models import MatterEngagement, MatterWebsiteOverview
        from app.matters.work_items import response_obligation_of
        from app.submissions.enums import SubmissionStatus
        from app.submissions.models import Submission

        published = set(
            MatterWebsiteOverview.objects.visible_to(viewer)
            .filter(matter=matter, status=WebsiteOverviewStatus.PUBLISHED)
            .values_list("kind", flat=True)
        )
        engagements = MatterEngagement.objects.visible_to(viewer).filter(matter=matter)
        return cls(
            # A published write-up whose kind predates the choice reads as an
            # overview, which is what every one of them was filed as.
            published_overview=bool(published & {WebsiteOverviewKind.OVERVIEW, ""}),
            published_any=bool(published),
            engagement=engagements.exists(),
            feedback_recorded=engagements.filter(feedback_closed_at__isnull=False).exists(),
            opinion_sent=Submission.objects.visible_to(viewer)
            .filter(matter=matter, status=SubmissionStatus.SENT)
            .exists(),
            opinion_owed=response_obligation_of(matter, viewer).is_outstanding,
        )

    def proves(self, step: MatterPlanStep) -> bool:
        """Whether ``step`` is already done on the record. Unknown steps never are."""
        opinion_stands = self.opinion_sent and not self.opinion_owed
        key = step.template_step_key
        if key == "read-material":
            return self.published_any or self.engagement or self.opinion_sent
        if key == "form-position":
            return self.feedback_recorded or opinion_stands
        if step.operation == PlanStepOperation.WEBSITE_OVERVIEW:
            return self.published_overview
        if step.operation == PlanStepOperation.ENGAGEMENT:
            return self.engagement
        if step.operation == PlanStepOperation.SUBMISSION:
            return opinion_stands
        return False


def recommendation_for(
    matter: Any, current_action: Any, viewer: Any = None
) -> Recommendation | None:
    """The suggestion for this reader's page, or ``None``.

    ``None`` while a current step is visible — work already in hand is never
    shadowed by a suggestion — on a closed Matter, on an archive register row
    and when nothing is left ahead. Otherwise the first open step in order that
    the record does not already prove done (`RecordedWork`).
    """
    if current_action is not None or not matter.is_open:
        return None
    if matter.record_mode != RecordMode.FULL:
        return None
    steps = plan_steps_of(matter)
    # No reader-blind look at open actions here: a step whose open action is
    # restricted below this reader reads as any other step ahead, so the
    # suggestion cannot be used to learn about an action the reader may not
    # see (docs/adr/0133 §3). Starting it then refuses under the lock.
    open_steps = [step for step in steps if step.is_open]
    if not open_steps:
        return None
    done = RecordedWork.of(matter, viewer)
    ahead = next((step for step in open_steps if not done.proves(step)), None)
    if ahead is None:
        return None
    return Recommendation(step=ahead, revision=plan_revision(steps))
