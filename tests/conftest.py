from __future__ import annotations

import zipfile

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
def posix_zip_names(monkeypatch):
    """Read ZIP member names the way Linux does, on whatever host is running.

    ``zipfile`` rewrites the host's ``os.sep`` to ``/`` while parsing a member
    name, so on Windows a stored backslash never reaches the reader and a test
    written around one would hold whether or not the code handles it. The real
    opinions archive stores every member with a backslash, which is why this
    exists. Only the separator rewrite is removed; the null-byte truncation
    done in the same place is a real defence and is kept. On Linux this changes
    nothing, which is the point.
    """
    monkeypatch.setattr(
        zipfile, "_sanitize_filename", lambda name: name.split(chr(0))[0], raising=False
    )


@pytest.fixture
def pdf_bytes():
    """The smallest thing that passes the upload signature check."""
    return b"%PDF-1.4 synthetic evidence"


@pytest.fixture(autouse=True)
def evidence_root(settings, tmp_path_factory):
    """Evidence, derivatives and OneNote source in this test's own directory.

    Three directories rather than one, because keeping them apart is a property
    under test: a backup that omits derivatives must still be a complete backup,
    and that is only checkable if they were never mixed (Stage-2B brief 9, 81).

    ``autouse``, and that keyword is the whole point of this fixture now. It
    used to be opt-in, which meant a test was isolated only if its author
    remembered to ask — and on 2026-08-24 a suite run inside a container built
    from the production image wrote 63 synthetic fixtures into the Chamber's
    real evidence store, through tests that simply did not request it. Storage
    isolation is not a thing to remember; it is the floor.

    Tests that name ``evidence_root`` get the same three subdirectories under it
    they always did, so nothing about them changes.

    A directory of its own rather than the test's ``tmp_path``, which is the one
    thing autouse changed the meaning of: while this was opt-in, only a test
    that asked for storage got these three directories planted in its
    ``tmp_path``. Autouse plants them in *every* test's, including tests that
    use ``tmp_path`` as a scratch tree and mean something by what is in it —
    ``test_the_backup_refuses_a_data_root_with_no_evidence_tree`` passes its
    ``tmp_path`` to the backup script precisely because it holds no ``evidence``
    directory. Somewhere else entirely, per test, is the only version of this
    that is invisible.

    Per test rather than per process, so two tests can never see each other's
    files and a parallel run cannot share a writable tree: ``mktemp`` numbers
    each call, and each xdist worker has its own base directory.
    """
    root = tmp_path_factory.mktemp("juristid-storage")
    settings.EVIDENCE_ROOT = root / "evidence"
    settings.DERIVATIVE_ROOT = root / "derivatives"
    # The third canonical storage class. Left out while this fixture was opt-in,
    # which meant the OneNote page XML a historical-import test writes went to
    # whatever LEGACY_SOURCE_ROOT the settings named — a temporary directory
    # under the test settings, and `/app/legacy-source` under the production
    # ones. Same mount, same failure, one class of evidence further along.
    settings.LEGACY_SOURCE_ROOT = root / "legacy-source"
    # Created, not merely named. A deployment's storage roots exist before the
    # process starts — they are bind mounts — and `deployment_readiness` calls a
    # root that is absent a problem, correctly: a container handed an empty
    # directory where a mount should be works perfectly until it is replaced.
    # While this fixture was opt-in, most tests saw the directories
    # `config/test_settings.py` had already made with `mkdtemp`, so nothing
    # noticed. Storage that exists is part of what "isolated" has to mean here.
    for storage_root in (
        settings.EVIDENCE_ROOT,
        settings.DERIVATIVE_ROOT,
        settings.LEGACY_SOURCE_ROOT,
    ):
        storage_root.mkdir(parents=True, exist_ok=True)
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
        settings.LEGACY_SOURCE_STORAGE_ALIAS: {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(settings.LEGACY_SOURCE_ROOT)},
        },
    }
    return root


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
def responsibility_world(world):
    """The shared world plus register-backed responsibility.

    Opt-in, so the existing suite's hand-derived counts stay true. See
    ``tests/synthetic_statistics.add_responsibility_world``.
    """
    from tests.synthetic_statistics import add_responsibility_world

    return add_responsibility_world(world)


@pytest.fixture
def archive_world(world):
    """The shared world plus a small opinions archive.

    Opt-in for the same reason, and because the world *without* it is what the
    empty-archive tests assert against (brief 76).
    """
    from tests.synthetic_statistics import add_archive_world

    return add_archive_world(world)


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
