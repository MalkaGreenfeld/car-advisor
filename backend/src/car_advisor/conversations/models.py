from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Unicode,
    UnicodeText,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from car_advisor.db import Base

# A SQLite unique index treats NULLs as distinct, while SQL Server treats them as equal.
# Storing "no car" as this non-NULL value makes ux_rep_questions_open allow only one open
# car-less question per conversation and field on both databases.
NO_CAR: Final = ""

_OPEN_ONLY = text("status = 'open'")


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime]
    employee_number: Mapped[int]
    profile: Mapped[str] = mapped_column(UnicodeText)
    alternatives: Mapped[str] = mapped_column(UnicodeText)
    comparison: Mapped[str | None] = mapped_column(UnicodeText)
    agent_history: Mapped[str | None] = mapped_column(UnicodeText)
    agent_run_started_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column()

    __mapper_args__ = {"version_id_col": version}


class MessageRow(Base):
    __tablename__ = "messages"

    # SQLite autoincrements only a column declared exactly INTEGER PRIMARY KEY.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str | None] = mapped_column(UnicodeText)
    notice_code: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime]


class RepQuestionRow(Base):
    __tablename__ = "rep_questions"
    __table_args__ = (
        Index(
            "ux_rep_questions_open",
            "conversation_id",
            "missing_field",
            "car_key",
            unique=True,
            mssql_where=_OPEN_ONLY,
            sqlite_where=_OPEN_ONLY,
        ),
        Index("ix_rep_questions_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    question_type: Mapped[str] = mapped_column(String(20))
    missing_field: Mapped[str] = mapped_column(String(30))
    car_key: Mapped[str] = mapped_column(String(40))
    car_display_name: Mapped[str | None] = mapped_column(Unicode(200))
    question_text: Mapped[str] = mapped_column(Unicode(1000))
    employee_display_name: Mapped[str | None] = mapped_column(Unicode(200))
    employee_number: Mapped[int]
    eligibility_group: Mapped[str | None] = mapped_column(CHAR(1))
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime]
    answered_at: Mapped[datetime | None]
    participation_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    participation_effective_date: Mapped[date | None]
    answer_note: Mapped[str | None] = mapped_column(Unicode(500))
    answer_text: Mapped[str | None] = mapped_column(Unicode(2000))
