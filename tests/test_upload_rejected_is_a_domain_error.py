"""A rejected upload is a refused business operation, like every other (JARCH-006).

`UploadRejected` was a plain `Exception`, so every view that took files had to
catch it explicitly: `except (DomainError, UploadRejected)`. All 26 such sites
did. The next upload door to catch only `DomainError` would have turned a
rejected file into a 500 instead of the refusal sentence beside the form
(2026-10-17 audit). The upload refusal is now a `DomainError`, so the
ordinary handler is enough.

And one reply form, under one name (JARCH-009): `EngagementReplyForm` was an
alias of `FeedbackForm` left by the owner's UX round, stage 3.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.errors import DomainError
from app.documents.uploads import UploadRejected
from app.matters import forms as matter_forms
from tests.refusals import refused

APP = Path(__file__).resolve().parents[1] / "app"


def test_an_upload_refusal_is_a_domain_error() -> None:
    assert issubclass(UploadRejected, DomainError)


def test_a_handler_for_domain_errors_answers_a_rejected_upload() -> None:
    def door() -> str:
        try:
            raise UploadRejected("Fail on tühi.")
        except DomainError as error:
            return str(error)

    assert door() == "Fail on tühi."


def test_the_refusal_keeps_its_own_type_and_sentence() -> None:
    with refused("Fail on tühi.") as caught:
        raise UploadRejected("Fail on tühi.")
    assert caught.type is UploadRejected


def _handled(handler: ast.ExceptHandler) -> set[str]:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {kind.id for kind in kinds if isinstance(kind, ast.Name)}


def test_no_handler_still_names_both() -> None:
    """`except (DomainError, UploadRejected)` now says one thing twice."""
    doubled = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and {"DomainError", "UploadRejected"} <= (
                _handled(node)
            ):
                doubled.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert not doubled, doubled


def test_the_reply_form_has_one_name() -> None:
    assert not hasattr(matter_forms, "EngagementReplyForm")
    assert {"provenance", "engagement"} <= set(matter_forms.FeedbackForm.base_fields)
