"""add work_items, work_item_events and session_related_projects tables

Revision ID: za1b2c3d4e5f6
Revises: c4d5e6f7a8b9
Create Date: 2026-08-06 00:00:00.000000

Adds the durable, provider-neutral session Work Tree: ``work_items`` (the tree
itself), ``work_item_events`` (its sanitized append-only audit trail), and
``session_related_projects`` (zero-or-more secondary projects a session touches
alongside its first-class home project).

Also widens ``omnigent_conversation_metadata`` with the explicit user lifecycle
and the provider-neutral permission/tool-profile contract. ``user_lifecycle`` is
nullable so existing sessions keep behaving exactly as before — NULL means the
user has never chosen, and callers read that as "active". The profile columns
hold names/state only, never a credential.

Existing sessions gain no ``work_items`` rows, so they read as an empty tree.

All three tables are brand-new and are created at the current schema state, so
each carries the tenant-partition ``workspace_id`` column as the leading
primary-key member. There are no foreign-key constraints (schema Rule R032 —
see ``p1a2b3c4d5e6``): the ``conversation_id`` / ``parent_id`` / ``project_id``
relationships are enforced by the application, not the database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from omnigent.db.db_models import Uuid16

revision: str = "za1b2c3d4e5f6"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIFECYCLE_CHECK = "ck_conversation_metadata_user_lifecycle"


def check_constraints_supported(dialect: sa.engine.Dialect) -> bool:
    """Whether a named CHECK constraint will really exist on this dialect.

    MySQL below 8.0.16 (MariaDB below 10.2.1) parses ``CHECK`` and then silently
    ignores it, so the constraint is never created — and the downgrade's drop
    then fails on a constraint that was never there. Dialects we have not
    verified are treated as unsupported: skipping an advisory constraint is
    recoverable, a migration that aborts halfway through a column drop is not.

    :param dialect: The bound dialect the migration is running against.
    :returns: ``True`` when the CHECK should be created and later dropped.
    """
    name = dialect.name
    if name in ("sqlite", "postgresql"):
        return True
    if name in ("mysql", "mariadb"):
        version = getattr(dialect, "server_version_info", None) or ()
        if getattr(dialect, "is_mariadb", False):
            return tuple(version) >= (10, 2, 1)
        return tuple(version) >= (8, 0, 16)
    return False


def _lifecycle_check_present(bind: sa.engine.Connection) -> bool:
    """Whether ``ck_conversation_metadata_user_lifecycle`` is really there.

    Reflection, not assumption: a database that skipped the constraint on
    upgrade — or one restored from a dump that dropped it — must still
    downgrade cleanly.

    :param bind: The migration's connection.
    :returns: ``True`` when the constraint exists and can be dropped.
    """
    if not check_constraints_supported(bind.dialect):
        return False
    try:
        constraints = sa.inspect(bind).get_check_constraints("omnigent_conversation_metadata")
    except (NotImplementedError, sa.exc.SQLAlchemyError):
        return False
    return any(c.get("name") == _LIFECYCLE_CHECK for c in constraints)


def upgrade() -> None:
    """Create the Work Tree tables and widen conversation metadata."""
    op.create_table(
        "work_items",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        # UUID PK + self-ref + project ref stored as 16 raw bytes (Uuid16 →
        # BINARY(16) on MySQL, BLOB/BYTEA elsewhere).
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("parent_id", Uuid16(), nullable=True),
        # Denormalized 1-based depth so the max-depth rule is a cheap CHECK.
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        # Opaque free text stored compressed (CompressedText → LargeBinary).
        sa.Column("brief", sa.LargeBinary(), nullable=True),
        sa.Column("why", sa.LargeBinary(), nullable=True),
        sa.Column("next_action", sa.LargeBinary(), nullable=True),
        sa.Column("evidence", sa.LargeBinary(), nullable=True),
        # Enums stored as stable int codes (see omnigent.db.enum_codecs
        # WORK_ITEM_STATUS: not_started=1, working=2, waiting=3, paused=4,
        # blocked=5, done=6).
        sa.Column("status", sa.SmallInteger(), nullable=False),
        # WORK_ITEM_DELIVERY_STATE: local=1 … closed=10. NULL = no delivery
        # meaning yet; deliberately independent of status.
        sa.Column("delivery_state", sa.SmallInteger(), nullable=True),
        # WORK_ITEM_SOURCE_KIND: user=1, orchestrator=2, worker=3,
        # provider_todo=4, discovered=5, resumed=6.
        sa.Column("source_kind", sa.SmallInteger(), nullable=False),
        # WORK_ITEM_DISCOVERY_CLASS: required=1, related_later=2, unrelated=3,
        # scope_change=4.
        sa.Column("discovery_class", sa.SmallInteger(), nullable=True),
        sa.Column("project_id", Uuid16(), nullable=True),
        # Safe provider handle for re-matching across restarts; never a payload.
        sa.Column("source_ref", sa.String(256), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.Column("deferred_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN (1, 2, 3, 4, 5, 6)", name="ck_work_items_status"),
        sa.CheckConstraint(
            "delivery_state IS NULL OR delivery_state IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)",
            name="ck_work_items_delivery_state",
        ),
        sa.CheckConstraint("source_kind IN (1, 2, 3, 4, 5, 6)", name="ck_work_items_source_kind"),
        sa.CheckConstraint(
            "discovery_class IS NULL OR discovery_class IN (1, 2, 3, 4)",
            name="ck_work_items_discovery_class",
        ),
        # Session/programme → task → subtask; depth 4 fails at the DB too.
        sa.CheckConstraint("depth IN (1, 2, 3)", name="ck_work_items_depth"),
        sa.CheckConstraint(
            "(parent_id IS NULL AND depth = 1) OR (parent_id IS NOT NULL AND depth > 1)",
            name="ck_work_items_root_depth",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_work_items_conversation_id",
        "work_items",
        ["workspace_id", "conversation_id", "parent_id", "sort_order", "id"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_source_ref",
        "work_items",
        ["workspace_id", "conversation_id", "source_ref"],
        unique=False,
    )

    op.create_table(
        "work_item_events",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("id", Uuid16(), nullable=False),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("item_id", Uuid16(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        # A user id, or a provider label like "provider:claude".
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        # Short, server-authored phrase — never raw provider text.
        sa.Column("summary", sa.String(512), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        # Per-session monotonic counter: created_at is epoch seconds, too coarse
        # to order several edits made within the same second.
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_work_item_events_conversation_id",
        "work_item_events",
        ["workspace_id", "conversation_id", "seq", "item_id"],
        unique=False,
    )
    # ``seq`` is allocated from the session's current high-water mark, so two
    # concurrent mutations can read the same number. The database refuses the
    # second write and the store re-reads and retries, rather than leaving two
    # audit entries claiming the same position in the trail.
    op.create_index(
        "uq_work_item_events_conversation_seq",
        "work_item_events",
        ["workspace_id", "conversation_id", "seq"],
        unique=True,
    )

    op.create_table(
        "session_related_projects",
        sa.Column("workspace_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conversation_id", Uuid16(), nullable=False),
        sa.Column("project_id", Uuid16(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "project_id"),
    )
    op.create_index(
        "ix_session_related_projects_project_id",
        "session_related_projects",
        ["workspace_id", "project_id", "conversation_id"],
        unique=False,
    )

    # Explicit user lifecycle + the provider-neutral permission/tool-profile
    # contract. All nullable, so existing sessions are unchanged.
    #
    # Batched: SQLite cannot ALTER a table to add a CHECK, and batch mode
    # rebuilds the table so the constraint lands on every dialect rather than
    # being skipped on SQLite. One batch = one rebuild for all five columns.
    #
    # The CHECK is skipped where it would not be enforced anyway (see
    # ``check_constraints_supported``); the columns and the application-level
    # vocabulary are identical either way.
    supports_check = check_constraints_supported(op.get_bind().dialect)
    with op.batch_alter_table("omnigent_conversation_metadata") as batch_op:
        batch_op.add_column(sa.Column("user_lifecycle", sa.SmallInteger(), nullable=True))
        batch_op.add_column(
            sa.Column("requested_permission_profile", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("effective_permission_profile", sa.String(64), nullable=True)
        )
        batch_op.add_column(sa.Column("requested_tool_profile", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("effective_tool_profile", sa.String(64), nullable=True))
        if supports_check:
            batch_op.create_check_constraint(
                _LIFECYCLE_CHECK,
                "user_lifecycle IS NULL OR user_lifecycle IN (1, 2, 3, 4, 5)",
            )


def downgrade() -> None:
    """Drop the Work Tree tables and the conversation-metadata columns."""
    # Batched for the same reason as ``upgrade``: SQLite rejects a raw
    # ``ALTER TABLE ... DROP COLUMN`` before 3.35.
    #
    # The CHECK is dropped only when it is really there. On SQLite it always is
    # and must go, because batch mode rebuilds the table and would otherwise
    # carry a constraint referencing the column being dropped; on a MySQL build
    # that ignored it, dropping it would abort the batch part-way through.
    drop_check = _lifecycle_check_present(op.get_bind())
    with op.batch_alter_table("omnigent_conversation_metadata") as batch_op:
        if drop_check:
            batch_op.drop_constraint(_LIFECYCLE_CHECK, type_="check")
        batch_op.drop_column("effective_tool_profile")
        batch_op.drop_column("requested_tool_profile")
        batch_op.drop_column("effective_permission_profile")
        batch_op.drop_column("requested_permission_profile")
        batch_op.drop_column("user_lifecycle")
    op.drop_index("ix_session_related_projects_project_id", table_name="session_related_projects")
    op.drop_table("session_related_projects")
    op.drop_index("uq_work_item_events_conversation_seq", table_name="work_item_events")
    op.drop_index("ix_work_item_events_conversation_id", table_name="work_item_events")
    op.drop_table("work_item_events")
    op.drop_index("ix_work_items_source_ref", table_name="work_items")
    op.drop_index("ix_work_items_conversation_id", table_name="work_items")
    op.drop_table("work_items")
