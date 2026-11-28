"""Accounting under concurrent finalization (R98-C07, R98-08)."""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace

import pytest
from django.db import connection

from app.ai_usage import recorder
from app.ai_usage.models import AiAttempt, AiLedgerGap, AiOperation, Outcome

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _response() -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=10, output_tokens=2)
    return SimpleNamespace(model="m", stop_reason="end_turn", usage=usage)


def test_many_threads_finalizing_the_same_operation_leave_one_row_each():
    operation_id = uuid.uuid4()
    errors: list[BaseException] = []

    def work() -> None:
        try:
            rec = recorder.Recorder("candidates", "m", 1, operation_id=operation_id)
            rec.finish(response=_response(), outcome=Outcome.SUCCEEDED, classified=1)
        except BaseException as error:  # the recorder must not raise at all
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert AiOperation.objects.filter(pk=operation_id).count() == 1
    assert AiAttempt.objects.filter(operation_id=operation_id).count() == 1
    assert AiLedgerGap.objects.count() == 0
