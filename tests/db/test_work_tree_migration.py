"""Upgrade/downgrade safety for the Work Tree migration (``za1b2c3d4e5f6``).

The migration adds three tables and widens ``omnigent_conversation_metadata``
with a lifecycle CHECK. A CHECK is the one piece of that DDL whose behaviour is
not the same everywhere: MySQL below 8.0.16 (MariaDB below 10.2.1) parses
``CHECK`` and then silently ignores it, so the constraint is never created — and
a downgrade that unconditionally drops it aborts part-way through the column
drops, leaving the table half-migrated.

By default no Postgres or MySQL server is reachable (no driver installed and
``OMNIGENT_TEST_DB_URI`` unset), so most of these tests prove what *can* be
proven without one: the dialect decision itself, and the downgrade tolerating a
database where the constraint is absent. Point ``OMNIGENT_TEST_DB_URI`` at a
real server and :func:`test_the_revision_round_trips_on_the_external_database`
additionally runs the same round trip there, on that server's own DDL.
"""

from __future__ import annotations

import importlib.util
import os
import re
import tempfile
import warnings
from collections.abc import Generator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import omnigent.db
from omnigent.db.db_models import Uuid16

_REVISION = "za1b2c3d4e5f6"
_PREVIOUS = "c4d5e6f7a8b9"
_CHECK = "ck_conversation_metadata_user_lifecycle"
_METADATA_TABLE = "omnigent_conversation_metadata"
_NEW_COLUMNS = (
    "user_lifecycle",
    "requested_permission_profile",
    "effective_permission_profile",
    "requested_tool_profile",
    "effective_tool_profile",
)

_VERSIONS_DIR = Path(omnigent.db.__file__).parent / "migrations" / "versions"


def _load_migration() -> ModuleType:
    """Import the revision module directly; ``versions/`` is not a package.

    :returns: The loaded migration module.
    """
    path = next(_VERSIONS_DIR.glob(f"{_REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location(f"_migration_{_REVISION}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(uri: str) -> Config:
    """Alembic config pointed at the real script tree but our own database."""
    config = Config()
    config.set_main_option(
        "script_location", str(Path(omnigent.db.__file__).parent / "migrations")
    )
    config.set_main_option("sqlalchemy.url", uri)
    return config


def _check_names(engine: sa.Engine) -> set[str]:
    """Reflected CHECK constraint names on the conversation metadata table."""
    with engine.connect() as conn:
        return {c.get("name") for c in sa.inspect(conn).get_check_constraints(_METADATA_TABLE)}


def _column_names(engine: sa.Engine, table: str) -> set[str]:
    """Reflected column names for ``table``."""
    with engine.connect() as conn:
        return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _without_check(ddl: str, name: str) -> str:
    """Return ``ddl`` with the named inline CHECK constraint clause removed.

    Scans to the matching close paren rather than pattern-matching, because the
    constraint body has nested parentheses of its own.

    :param ddl: The table's ``CREATE TABLE`` statement.
    :param name: The constraint name to strip.
    :returns: The same DDL without that clause.
    """
    start = ddl.index(f"CONSTRAINT {name}")
    depth = 0
    for offset, char in enumerate(ddl[start:], start):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = offset + 1
                break
    else:  # pragma: no cover - unbalanced DDL would be a SQLite bug
        raise AssertionError(f"unbalanced CHECK clause for {name}")
    head = ddl[:start].rstrip().rstrip(",")
    return head + ddl[end:]


def _supports_check(engine: sa.Engine) -> bool:
    """Ask the migration's own guard about ``engine``'s server.

    A lazy engine has no ``server_version_info`` until something connects, and
    the guard reads exactly that — so connect first or every MySQL server looks
    like a pre-8.0.16 one that never enforces CHECK.
    """
    with engine.connect() as conn:
        return _load_migration().check_constraints_supported(conn.dialect)


def _fake_dialect(name: str, version: tuple[int, ...] = (), *, mariadb: bool = False):
    """A stand-in for a bound dialect, carrying only what the guard reads."""
    return SimpleNamespace(name=name, server_version_info=version, is_mariadb=mariadb)


# ── the dialect decision ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dialect", "supported"),
    [
        (_fake_dialect("sqlite", (3, 45, 0)), True),
        (_fake_dialect("postgresql", (16, 2)), True),
        # MySQL only started enforcing CHECK in 8.0.16; before that it parses
        # the clause and throws it away, so the constraint never exists.
        (_fake_dialect("mysql", (8, 0, 16)), True),
        (_fake_dialect("mysql", (8, 0, 15)), False),
        (_fake_dialect("mysql", (5, 7, 44)), False),
        (_fake_dialect("mysql", ()), False),
        (_fake_dialect("mysql", (10, 6, 0), mariadb=True), True),
        (_fake_dialect("mysql", (10, 1, 48), mariadb=True), False),
        # An unverified dialect is assumed not to support it: skipping an
        # advisory constraint is recoverable, a half-run downgrade is not.
        (_fake_dialect("databricks"), False),
    ],
)
def test_check_constraint_support_is_decided_per_dialect(dialect, supported: bool) -> None:
    """The guard names exactly the databases where the CHECK really exists."""
    migration = _load_migration()
    assert migration.check_constraints_supported(dialect) is supported


# ── SQLite: the constraint is created, dropped, and recreated ─────────────


def test_the_revision_round_trips_on_sqlite() -> None:
    """Upgrade, downgrade one step, and upgrade again on a disposable database.

    Proves the constraint and the five metadata columns really appear, really
    go away, and really come back — the SQLite path is the one shipped in every
    local ``chat.db``, so it is the one that must be exactly reversible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        uri = f"sqlite:///{Path(tmp) / 'work-tree.db'}"
        config = _config(uri)
        engine = sa.create_engine(uri)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                command.upgrade(config, _REVISION)

            assert _CHECK in _check_names(engine)
            assert set(_NEW_COLUMNS) <= _column_names(engine, _METADATA_TABLE)
            with engine.connect() as conn:
                indexes = {i["name"] for i in sa.inspect(conn).get_indexes("work_item_events")}
            assert "uq_work_item_events_conversation_seq" in indexes

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                command.downgrade(config, _PREVIOUS)

            assert _CHECK not in _check_names(engine)
            assert not (set(_NEW_COLUMNS) & _column_names(engine, _METADATA_TABLE))
            with engine.connect() as conn:
                tables = set(sa.inspect(conn).get_table_names())
            assert not ({"work_items", "work_item_events"} & tables)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                command.upgrade(config, _REVISION)
            assert _CHECK in _check_names(engine)
            assert set(_NEW_COLUMNS) <= _column_names(engine, _METADATA_TABLE)
        finally:
            engine.dispose()


# ── Postgres / MySQL: the same round trip on a real server ────────────────


@pytest.fixture()
def external_migration_uri() -> Generator[str, None, None]:
    """A throwaway database on the server ``OMNIGENT_TEST_DB_URI`` points at.

    The revision is exercised from ``base``, so it needs a database of its own
    rather than the migrated per-worker one the store tests share.
    """
    base_uri = os.environ.get("OMNIGENT_TEST_DB_URI", "")
    if not base_uri:
        pytest.skip("OMNIGENT_TEST_DB_URI is unset; no external database to migrate")

    worker = os.environ.get("PYTEST_XDIST_WORKER", "w0")
    name = f"omnigent_wt_migration_{worker}"
    uri = re.sub(r"/[^/]*(\?.*)?$", f"/{name}", base_uri)
    root = sa.create_engine(base_uri, isolation_level="AUTOCOMMIT")
    mysql = root.dialect.name == "mysql"
    quoted = f"`{name}`" if mysql else f'"{name}"'
    try:
        with root.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {quoted}"))
            conn.execute(sa.text(f"CREATE DATABASE {quoted}"))
        yield uri
    finally:
        with root.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {quoted}"))
        root.dispose()


def test_the_revision_round_trips_on_the_external_database(external_migration_uri: str) -> None:
    """Upgrade, downgrade one step, and upgrade again on Postgres or MySQL.

    SQLite rebuilds a table for almost any ALTER, so it cannot show whether the
    real ``ALTER TABLE ... DROP COLUMN`` path and the server's own CHECK
    handling survive a downgrade. Only a real server can.
    """
    config = _config(external_migration_uri)
    engine = sa.create_engine(external_migration_uri)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            command.upgrade(config, _REVISION)

        supports_check = _supports_check(engine)
        # Both servers under test enforce CHECK; a skip here would hide a
        # constraint that silently never got created.
        assert supports_check, f"{engine.dialect.name} unexpectedly skips CHECK constraints"
        assert set(_NEW_COLUMNS) <= _column_names(engine, _METADATA_TABLE)
        if supports_check:
            assert _CHECK in _check_names(engine)
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            assert {"work_items", "work_item_events", "session_related_projects"} <= set(
                inspector.get_table_names()
            )
            indexes = {i["name"] for i in inspector.get_indexes("work_item_events")}
            unique = {i["name"] for i in inspector.get_indexes("work_item_events") if i["unique"]}
        assert "uq_work_item_events_conversation_seq" in indexes
        assert "uq_work_item_events_conversation_seq" in unique

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            command.downgrade(config, _PREVIOUS)

        assert not (set(_NEW_COLUMNS) & _column_names(engine, _METADATA_TABLE))
        with engine.connect() as conn:
            tables = set(sa.inspect(conn).get_table_names())
        assert not ({"work_items", "work_item_events", "session_related_projects"} & tables)
        if supports_check:
            assert _CHECK not in _check_names(engine)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            command.upgrade(config, _REVISION)
        assert set(_NEW_COLUMNS) <= _column_names(engine, _METADATA_TABLE)
        if supports_check:
            assert _CHECK in _check_names(engine)
    finally:
        engine.dispose()


def test_the_lifecycle_check_is_enforced_on_the_external_database(
    external_migration_uri: str,
) -> None:
    """A bad ``user_lifecycle`` value is refused where the CHECK really exists.

    The SQLite tests prove the constraint is *created*; this proves the server
    actually rejects a write with it, which is the only reason it is there.
    """
    engine = sa.create_engine(external_migration_uri)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            command.upgrade(_config(external_migration_uri), _REVISION)
        if not _supports_check(engine):
            pytest.skip(f"{engine.dialect.name} does not enforce CHECK constraints here")

        # A typed table, not raw SQL: ids are raw bytes on disk (BINARY(16) on
        # MySQL), so a bare hex string bound as text overflows the column.
        table = sa.Table(
            _METADATA_TABLE,
            sa.MetaData(),
            sa.Column("workspace_id", sa.BigInteger, primary_key=True),
            sa.Column("id", Uuid16, primary_key=True),
            sa.Column("kind", sa.SmallInteger),
            sa.Column("user_lifecycle", sa.SmallInteger),
        )
        row = {"workspace_id": 0, "kind": 1}
        # 1..5 are the encoded lifecycle values; NULL means "never chosen".
        with engine.begin() as conn:
            conn.execute(table.insert(), {**row, "id": "a" * 32, "user_lifecycle": 1})
            conn.execute(table.insert(), {**row, "id": "b" * 32, "user_lifecycle": None})
        # DBAPIError, not IntegrityError: Postgres reports a violated CHECK as an
        # integrity error while MySQL reports it as an operational one (3819).
        # The constraint name in the message is what both agree on.
        with pytest.raises(sa.exc.DBAPIError) as refused:
            with engine.begin() as conn:
                conn.execute(table.insert(), {**row, "id": "c" * 32, "user_lifecycle": 99})
        assert _CHECK in str(refused.value)
    finally:
        engine.dispose()


def test_downgrade_tolerates_a_database_without_the_check() -> None:
    """The downgrade still completes where the CHECK was never created.

    Stands in for a MySQL build that ignored the constraint: the table is
    rebuilt without it, and the downgrade must notice and drop the columns
    anyway instead of failing on a constraint that is not there. Reproducing the
    absence is the closest this suite can get to that server — no MySQL or
    Postgres is reachable from here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        uri = f"sqlite:///{Path(tmp) / 'no-check.db'}"
        config = _config(uri)
        engine = sa.create_engine(uri)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                command.upgrade(config, _REVISION)
            assert _CHECK in _check_names(engine)

            # Rebuild the table from its own DDL with just the CHECK clause
            # removed, so every column keeps its type and the constraint is
            # genuinely absent from the schema the downgrade reflects.
            with engine.connect() as conn:
                ddl = conn.execute(
                    sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :n"),
                    {"n": _METADATA_TABLE},
                ).scalar_one()
            rebuilt = _without_check(ddl, _CHECK).replace(_METADATA_TABLE, "_rebuilt", 1)
            with engine.begin() as conn:
                conn.execute(sa.text(rebuilt))
                conn.execute(sa.text(f"INSERT INTO _rebuilt SELECT * FROM {_METADATA_TABLE}"))
                conn.execute(sa.text(f"DROP TABLE {_METADATA_TABLE}"))
                conn.execute(sa.text(f"ALTER TABLE _rebuilt RENAME TO {_METADATA_TABLE}"))
            assert _CHECK not in _check_names(engine)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                command.downgrade(config, _PREVIOUS)

            assert not (set(_NEW_COLUMNS) & _column_names(engine, _METADATA_TABLE))
        finally:
            engine.dispose()
