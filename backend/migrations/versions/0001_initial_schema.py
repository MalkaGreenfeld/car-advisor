"""Create the usage_values, conversations, messages and rep_questions tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0001"
down_revision: str | None = None

# The migration keeps its own copies of the model types so that later model changes
# cannot rewrite this revision.
DATETIME = sa.DateTime().with_variant(mssql.DATETIME2(), "mssql")
# SQLite autoincrements only a column declared exactly INTEGER PRIMARY KEY.
MESSAGE_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
OPEN_ONLY = sa.text("status = 'open'")


def upgrade() -> None:
    op.create_table(
        "usage_values",
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("registration_year", sa.Integer(), nullable=False),
        sa.Column("vehicle_type_code", sa.String(10), nullable=False),
        sa.Column("manufacturer_code", sa.String(10), nullable=False),
        sa.Column("model_code", sa.String(10), nullable=False),
        sa.Column("manufacturer_name", sa.Unicode(100), nullable=False),
        sa.Column("model_description", sa.Unicode(100), nullable=False),
        sa.Column("fuel_type", sa.String(10), nullable=False),
        sa.Column("trade_name", sa.Unicode(100), nullable=False),
        sa.Column("automatic", sa.Integer(), nullable=False),
        sa.Column("electric_raw", sa.Integer(), nullable=False),
        sa.Column("green_score", sa.Integer(), nullable=False),
        sa.Column("pollution_level", sa.Integer(), nullable=False),
        sa.Column("engine_cc", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("list_price", sa.Integer(), nullable=False),
        sa.Column("adjusted_price", sa.Integer(), nullable=False),
        sa.Column("usage_value", sa.Integer(), nullable=False),
        sa.Column("powertrain", sa.String(12), nullable=False),
        sa.PrimaryKeyConstraint(
            "tax_year",
            "registration_year",
            "vehicle_type_code",
            "manufacturer_code",
            "model_code",
        ),
    )
    op.create_index(
        "ix_usage_values_tax_year_registration_year",
        "usage_values",
        ["tax_year", "registration_year"],
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", DATETIME, nullable=False),
        sa.Column("employee_number", sa.Integer(), nullable=False),
        sa.Column("profile", sa.UnicodeText(), nullable=False),
        sa.Column("alternatives", sa.UnicodeText(), nullable=False),
        sa.Column("comparison", sa.UnicodeText(), nullable=True),
        sa.Column("agent_history", sa.UnicodeText(), nullable=True),
        sa.Column("agent_run_started_at", DATETIME, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "messages",
        sa.Column("id", MESSAGE_ID, nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("text", sa.UnicodeText(), nullable=True),
        sa.Column("notice_code", sa.String(30), nullable=True),
        sa.Column("created_at", DATETIME, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "rep_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("question_type", sa.String(20), nullable=False),
        sa.Column("missing_field", sa.String(30), nullable=False),
        sa.Column("car_key", sa.String(40), nullable=False),
        sa.Column("car_display_name", sa.Unicode(200), nullable=True),
        sa.Column("question_text", sa.Unicode(1000), nullable=False),
        sa.Column("employee_display_name", sa.Unicode(200), nullable=True),
        sa.Column("employee_number", sa.Integer(), nullable=False),
        sa.Column("eligibility_group", sa.CHAR(1), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", DATETIME, nullable=False),
        sa.Column("answered_at", DATETIME, nullable=True),
        sa.Column("participation_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("participation_effective_date", sa.Date(), nullable=True),
        sa.Column("answer_note", sa.Unicode(500), nullable=True),
        sa.Column("answer_text", sa.Unicode(2000), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_rep_questions_open",
        "rep_questions",
        ["conversation_id", "missing_field", "car_key"],
        unique=True,
        mssql_where=OPEN_ONLY,
        sqlite_where=OPEN_ONLY,
    )
    op.create_index("ix_rep_questions_status_created", "rep_questions", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rep_questions_status_created", "rep_questions")
    op.drop_index("ux_rep_questions_open", "rep_questions")
    op.drop_table("rep_questions")
    op.drop_index("ix_messages_conversation_id", "messages")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_index("ix_usage_values_tax_year_registration_year", "usage_values")
    op.drop_table("usage_values")
