from __future__ import annotations

import pytest

from tests import factories


@pytest.fixture
def specialist(db):
    return factories.UserFactory()


@pytest.fixture
def other_specialist(db):
    return factories.UserFactory()


@pytest.fixture
def department_head(db):
    return factories.DepartmentHeadFactory()


@pytest.fixture
def administrator(db):
    return factories.AdministratorFactory()


@pytest.fixture
def superuser(db):
    return factories.AdministratorFactory(is_superuser=True)


@pytest.fixture
def normal_matter(db, specialist):
    return factories.MatterFactory(owner=specialist)


@pytest.fixture
def restricted_matter(db, specialist):
    from app.core.enums import Visibility

    return factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)


@pytest.fixture
def organisation(db):
    return factories.OrganisationFactory()


@pytest.fixture
def stage(db):
    return factories.StageFactory()


@pytest.fixture
def signed_in(client, specialist):
    """A client signed in as an ordinary specialist."""
    client.force_login(specialist)
    return client


@pytest.fixture
def pdf_bytes():
    """The smallest thing that passes the upload signature check."""
    return b"%PDF-1.4 synthetic evidence"


@pytest.fixture
def evidence_root(settings, tmp_path):
    """Evidence and derivatives in a temporary directory, kept apart.

    Two directories rather than one, because keeping them apart is a property
    under test: a backup that omits derivatives must still be a complete backup,
    and that is only checkable if they were never mixed (Stage-2B brief 9, 81).
    """
    settings.EVIDENCE_ROOT = tmp_path / "evidence"
    settings.DERIVATIVE_ROOT = tmp_path / "derivatives"
    settings.STORAGES = {
        **settings.STORAGES,
        settings.EVIDENCE_STORAGE_ALIAS: {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(settings.EVIDENCE_ROOT)},
        },
        settings.DERIVATIVE_STORAGE_ALIAS: {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(settings.DERIVATIVE_ROOT)},
        },
    }
    return tmp_path


@pytest.fixture
def capture_evidence(evidence_root):
    """Store one synthetic file as evidence and hand back its version.

    Goes through the real capture service rather than building rows directly,
    so every test that needs a file also exercises the checksum, the storage key
    and the immutability the rest of the system depends on.
    """
    from app.documents.services import add_evidence_version, create_document

    def capture(matter, content: bytes, filename: str, mime_type: str, **kwargs):
        document = create_document(
            matter=matter,
            title=kwargs.pop("title", filename),
            role=kwargs.pop("role", "INCOMING_AUTHORITY"),
            visibility_override=kwargs.pop("visibility_override", ""),
            created_by=kwargs.pop("created_by", None),
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            **kwargs,
        )
        return version

    return capture


@pytest.fixture
def extract():
    """Claim and process one version, the way the worker does."""
    from app.documents.extraction.orchestrator import claim_version, extract_document_version

    def run(version, *, force: bool = True):
        claimed = claim_version(version.pk, force=force)
        assert claimed is not None, "the version could not be claimed"
        return extract_document_version(claimed)

    return run


# ---------------------------------------------------------------------------
# Statistika
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db):
    """The synthetic department the Statistika suite reads.

    One fixture rather than a factory call per test: the metrics are about
    *populations*, and a test that built its own three records would assert
    against a world too small for the buckets that matter — Teadmata aasta, a
    duplicate SHA-256, a file waiting on a scanner (tests/synthetic_statistics.py).
    """
    from tests.synthetic_statistics import build_world

    return build_world()


@pytest.fixture
def reporting_context(world):
    """A context builder bound to the fixture's day.

    reporting_context(world.martin)                    # current year
    reporting_context(world.martin, period="koik")     # everything
    """
    from django.utils import timezone

    from app.reporting.context import ReportingContext, parse_period

    def build(viewer, *, period: str = "koik", **overrides):
        return ReportingContext(
            viewer=viewer,
            period=parse_period(period, world.today),
            today=world.today,
            now=timezone.now(),
            **overrides,
        )

    return build
