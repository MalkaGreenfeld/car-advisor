"""Add the choice answer of a rep question, and rename the occupation_waiver question type.

A question of the occupation type is answered with the name of an Occupations_Table entry, and one
of the eligibility group type with a group (Req 10.6). Both are stored in answer_choice.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"


def upgrade() -> None:
    op.add_column("rep_questions", sa.Column("answer_choice", sa.Unicode(200), nullable=True))
    op.execute(
        "UPDATE rep_questions SET question_type = 'occupation' "
        "WHERE question_type = 'occupation_waiver'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE rep_questions SET question_type = 'occupation_waiver' "
        "WHERE question_type = 'occupation'"
    )
    op.drop_column("rep_questions", "answer_choice")
