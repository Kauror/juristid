"""A daily photograph of the operational portfolio.

Almost every metric in this module is answered from canonical tables, which is
the rule (master specification 18.1, 18.9). This is the one exception, and it
exists because of a question those tables genuinely cannot answer: *how many
active Matters had no next action last March?*

Nothing records that. ``NextAction`` keeps its own history — a replaced action
is superseded rather than deleted — but a Matter that was closed since, or one
whose stage moved twice, cannot be reconstructed as it stood on a Tuesday in
March. A trend built by re-deriving the past from the present would be a
confident line describing today, drawn backwards.

So this table starts accumulating at cutover and never claims more. There is
deliberately **no backfill command**: a row here means "this was true on this
date and somebody looked", and a manufactured row would look exactly the same
while meaning nothing (Stage-2E brief 50, 52).

Three properties are load-bearing.

**It snapshots the operational population only** — open FULL Matters. Writing
several thousand archive rows every night would multiply the table by the size
of the archive to answer a question nobody asks about it.

**It never stores visibility.** Reading a snapshot statistic joins the *live*
Matter and authorizes there. If a Matter is restricted today, it disappears
from last month's aggregate too, for anybody who may not see it now — which is
the only safe direction. A stored copy of a visibility decision is a copy that
goes stale the moment somebody changes it, and this codebase has already
removed one such column for exactly that reason (docs/adr/0005).

**One row per Matter per date**, enforced by a constraint. The capture command
is therefore idempotent by construction rather than by remembering to check.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from app.workflow.enums import ActionKind, DateSemantics, Track


class OperationalMatterSnapshot(BaseModel):
    """What one active Matter looked like on one day."""

    snapshot_date = models.DateField(db_index=True, verbose_name="hetktõmmise kuupäev")
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="operational_snapshots",
        verbose_name="teema",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="operational_snapshots",
        verbose_name="vastutaja",
    )
    stage = models.ForeignKey(
        "workflow.StageVocabulary",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="operational_snapshots",
        verbose_name="hetkeseis",
    )
    # The key as text as well as the foreign key. A stage renamed in 2027 must
    # not silently rewrite what the March 2026 photograph said, and the label a
    # reader saw at the time is part of the record.
    stage_key = models.CharField(max_length=64, blank=True, verbose_name="hetkeseisu võti")
    stage_label = models.CharField(max_length=200, blank=True, verbose_name="hetkeseisu nimetus")

    track = models.CharField(
        max_length=32,
        choices=Track.choices,
        blank=True,
        default="",
        verbose_name="menetlusliik",
    )

    # The next action as three separate facts, exactly as the live model keeps
    # them. Collapsing kind and date meaning into one column here would
    # reintroduce, in the history, the ambiguity Stage 1 removed from the
    # present: only DO + DEADLINE can ever have been overdue.
    next_action_kind = models.CharField(
        max_length=16,
        choices=ActionKind.choices,
        blank=True,
        default="",
        verbose_name="järgmise tegevuse liik",
    )
    next_action_date_semantics = models.CharField(
        max_length=32,
        choices=DateSemantics.choices,
        blank=True,
        default="",
        verbose_name="kuupäeva tähendus",
    )
    next_action_date = models.DateField(null=True, blank=True, verbose_name="tegevuse kuupäev")
    response_deadline = models.DateField(null=True, blank=True, verbose_name="arvamuse tähtaeg")

    captured_at = models.DateTimeField(verbose_name="hõivatud")

    class Meta:
        verbose_name = "teema hetktõmmis"
        verbose_name_plural = "teemade hetktõmmised"
        ordering = ["-snapshot_date", "matter"]
        constraints = [
            # Idempotency as a database fact rather than a convention the
            # command has to remember. Running the capture twice on one day
            # updates rather than duplicates.
            models.UniqueConstraint(
                fields=["snapshot_date", "matter"],
                name="reporting_one_snapshot_per_matter_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot_date", "owner"], name="reporting_snapshot_owner"),
            models.Index(
                fields=["snapshot_date", "next_action_kind"],
                name="reporting_snapshot_action",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.snapshot_date:%Y-%m-%d} · {self.matter_id}"

    @property
    def has_next_action(self) -> bool:
        return bool(self.next_action_kind)

    def was_overdue(self) -> bool:
        """Whether this photograph shows genuinely late work.

        The same rule as the live model, and for the same reason: a WAIT whose
        review date had passed was due for a look, not missed, and a history
        that says otherwise would make a trend of "overdue work" meaningless.
        """
        if self.next_action_kind != ActionKind.DO:
            return False
        if self.next_action_date_semantics != DateSemantics.DEADLINE:
            return False
        if self.next_action_date is None:
            return False
        return self.next_action_date < self.snapshot_date
