"""PostgreSQL JIT is off for the Juristid database (AUD-039, docs/adr/0153).

``postgres:18`` ships ``jit = on`` with ``jit_above_cost = 100000``. JIT is
chosen from the planner's *estimated* cost, and this workload's short OLTP
statements crossed it as the portfolio grew: in the production image the
administrator's register spent 342 ms of 411 ms compiling, against 13.5 ms for
the same rows without it. Nothing in this product runs the long analytical scans
JIT pays off on.

Set on the database rather than only on the server command line, so every
environment that migrates — CI, a rehearsal, a restore into a server started
without ``-c jit=off`` — answers ``SHOW jit`` with ``off``. ``ALTER DATABASE``
reaches new sessions; the ``SET`` makes the session running the migration agree
at once. Reversible: ``RESET`` returns the database to the server's default.
No data is read or written.
"""

from django.db import migrations

JIT_OFF = """
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET jit = off', current_database());
END
$$;
SET jit = off;
"""

JIT_DEFAULT = """
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I RESET jit', current_database());
END
$$;
RESET jit;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunSQL(JIT_OFF, reverse_sql=JIT_DEFAULT)]
