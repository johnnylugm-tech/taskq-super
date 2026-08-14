"""Alembic migrations package root.

[FR-07] — the migration revisions live under `migrations.versions`
and are discovered by alembic via `script_location = migrations` in
`alembic.ini`. Each revision is a Python module providing `upgrade()`
and `downgrade()` callables; alembic loads them in linear order from
the `down_revision` chain.

Citations:
- migrations.versions.v1_initial: see migrations/versions/v1_initial.py
- migrations.versions.v2_tags: see migrations/versions/v2_tags.py
- migrations.versions.v3_split_results: see migrations/versions/v3_split_results.py
"""
