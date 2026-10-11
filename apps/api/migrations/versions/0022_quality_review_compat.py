"""Repair additive quality-review fields on databases stamped at older 0020/0021 builds."""

import sqlalchemy as sa
from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0022_quality_review_compat"
down_revision = "0021_quality_review_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add and backfill the source-review fields without changing review decisions."""
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)
    _add_column("quality_review_cases", "source_verified_at", sa.Float(), nullable=True)
    _add_column("quality_review_cases", "source_verified_hash", sa.String(64), nullable=True)
    _add_column("quality_review_cases", "source_verified_quote", sa.Text(), nullable=True)
    _add_column("quality_review_packages", "created_by_identity_verified", sa.Boolean(), nullable=True)
    _add_column("quality_review_decisions", "claimed_quote", sa.Text(), nullable=True)
    _add_column("quality_review_case_revisions", "created_by", sa.String(128), nullable=True)
    _add_column("quality_review_case_revisions", "created_by_identity_verified", sa.Boolean(), nullable=True)

    connection.execute(sa.text(
        "UPDATE quality_review_packages SET created_by_identity_verified = FALSE "
        "WHERE created_by_identity_verified IS NULL"
    ))
    connection.execute(sa.text(
        "UPDATE quality_review_decisions SET claimed_quote = '' WHERE claimed_quote IS NULL"
    ))
    connection.execute(sa.text("""
        UPDATE quality_review_case_revisions AS revision
        SET created_by = package.created_by
        FROM quality_review_cases AS review_case
        JOIN quality_review_packages AS package ON package.package_id = review_case.package_id
        WHERE revision.case_id = review_case.case_id AND revision.created_by IS NULL
    """))
    connection.execute(sa.text(
        "UPDATE quality_review_case_revisions SET created_by_identity_verified = FALSE "
        "WHERE created_by_identity_verified IS NULL"
    ))
    # A legacy revision table may predate the initial snapshot backfill. Preserve
    # each existing current case as its first immutable revision when absent.
    connection.execute(sa.text("""
        INSERT INTO quality_review_case_revisions (
            case_id, record_version, source_fingerprint, input_fingerprint,
            output_fingerprint, source_json, locator_json, input_json,
            expected_json, actual_json, created_by, created_by_identity_verified, created_at
        )
        SELECT review_case.case_id, review_case.record_version, review_case.source_fingerprint,
               review_case.input_fingerprint, review_case.output_fingerprint,
               review_case.source_json, review_case.locator_json, review_case.input_json,
               review_case.expected_json, review_case.actual_json, package.created_by,
               package.created_by_identity_verified, review_case.created_at
        FROM quality_review_cases AS review_case
        JOIN quality_review_packages AS package ON package.package_id = review_case.package_id
        ON CONFLICT (case_id, record_version) DO NOTHING
    """))
    for table, column in (
        ("quality_review_packages", "created_by_identity_verified"),
        ("quality_review_decisions", "claimed_quote"),
        ("quality_review_case_revisions", "created_by"),
        ("quality_review_case_revisions", "created_by_identity_verified"),
    ):
        op.alter_column(table, column, nullable=False)


def _add_column(table_name: str, name: str, column_type: sa.types.TypeEngine, *, nullable: bool) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    if name not in existing:
        op.add_column(table_name, sa.Column(name, column_type, nullable=nullable))


def downgrade() -> None:
    # Historical source verification and review audit data must remain available.
    pass
