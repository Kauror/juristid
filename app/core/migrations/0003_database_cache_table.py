"""The database cache's table exists wherever the schema does (L-02).

The deployed settings keep the cache in PostgreSQL (``config/settings.py``,
``CACHES``), in a table named ``core_cache``. Django does not migrate that
table: it is made by ``manage.py createcachetable``, and nothing in a fresh
install, a migrate-from-zero or CI's recovery rehearsal ever ran it. Without it
every ``cache.add`` raises, and the MCP OAuth ``/oauth/token`` and
``/oauth/revoke`` endpoints answer 500 (their rate limit and the client
assertion's replay memory both live in the cache). Production has the table —
it was made by hand once, and a restore carries it in the dump — so the gap
only showed on a database that had never been near production.

The statements are the ones ``createcachetable`` issues for PostgreSQL, written
out so ``sqlmigrate`` shows them and a later Django cannot change what this
migration did: a ``varchar(255)`` primary key, ``text`` value, an
``expires timestamptz`` and its index ``core_cache_expires``.
``tests/test_aud_w3_cache_table.py`` compares the result with a table
``createcachetable`` makes itself.

Like ``createcachetable``, it does nothing at all when a relation of that name
is already there: no lock is taken, no index is rebuilt and no row is read or
written. That is the state of every deployed database, production included
(``core_cache_pkey`` and ``core_cache_expires``), so applying it there changes
nothing.

The reverse is deliberately empty. The table predates this migration on every
deployed database, and the code before it reads and writes the table just as
this code does; dropping it on the way back would turn a rollback into the very
outage this migration closes, and take the live rate-limit counters and replay
memory with it.
"""

from django.db import migrations

CREATE_CACHE_TABLE = """
DO $$
BEGIN
    IF to_regclass('core_cache') IS NULL THEN
        CREATE TABLE "core_cache" (
            "cache_key" varchar(255) NOT NULL PRIMARY KEY,
            "value" text NOT NULL,
            "expires" timestamp with time zone NOT NULL
        );
        CREATE INDEX "core_cache_expires" ON "core_cache" ("expires");
    END IF;
END
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0002_jit_off_for_this_database")]

    operations = [migrations.RunSQL(CREATE_CACHE_TABLE, reverse_sql=migrations.RunSQL.noop)]
