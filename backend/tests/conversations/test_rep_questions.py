import secrets
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from car_advisor.conversations.models import NO_CAR, ConversationRow, RepQuestionRow
from car_advisor.conversations.rep_questions import (
    RepCar,
    RepField,
    RepQuestionStatus,
    RepQuestionType,
    RepQuestionView,
    StoredAnswer,
    list_for_rep,
    open_or_get,
)
from car_advisor.conversations.service import (
    ComparisonView,
    ConversationNotFound,
    ConversationService,
    MissingView,
    TodayView,
)
from car_advisor.engine import copy_he
from car_advisor.engine.config import CatalogCar
from car_advisor.engine.types import (
    CarAlternative,
    CarKey,
    EmployeeProfile,
    Group,
    MissingField,
    WaiverAlternative,
)
from car_advisor.rules.loader import AppConfig, load_config
from tests.conversations.conftest import CONFIG_DIR

# The shipped files, which the views read the catalog amount and the choices from.
SHIPPED = load_config(
    CONFIG_DIR / "rules.yaml", CONFIG_DIR / "car_catalog.yaml", CONFIG_DIR / "occupations.yaml"
)

EMPLOYEE_NUMBER = 4821
NOW = datetime(2026, 3, 1, 9, 30)

# Cars of the real dataset, and what the mock catalog says about them.
CURRENT_KEY = CarKey.parse("2026-2023-1-01229-00177")  # a 2023 Tesla Model 3, not in the catalog
ATTO_3_KEY = CarKey.parse("2026-2025-1-01014-00003")  # group C, "BYD Atto 3"
NIRO_KEY = CarKey.parse("2026-2025-1-00885-00619")  # group B, "Kia Niro"
ATTO_3 = CarAlternative(car_key=ATTO_3_KEY)
NIRO = CarAlternative(car_key=NIRO_KEY)
WAIVER = WaiverAlternative()

DISPLAY_NAME = "דנה לוי"
GROUP_C = "קבוצה ג"
# Not in the mock occupations table, which is what leaves the answer to the representative.
UNLISTED_OCCUPATION = "רופא שיניים"

# A fully populated group C employee. Its amounts are unlike each other, the employee number and
# the car keys, so that none of them can appear in a text by chance.
PROFILE: dict[str, object] = {
    "display_name": DISPLAY_NAME,
    "base_salary": "17391",
    "pay_components": [
        {"kind": "commission", "label": "עמלות רבעון", "amount": "2953.5"},
        {"kind": "bonus", "label": "מענק שנתי", "amount": "1847"},
    ],
    "pension_rate_percent": "6.37",
    "study_fund_rate_percent": "2.61",
    "credit_points": "2.83",
    "occupation": UNLISTED_OCCUPATION,
    "children_under_23": 3,
    "has_current_car": True,
    "current_car": {"manufacturer": "Zorbo", "model": "Quasar", "registration_year": 2023},
    "current_car_key": str(CURRENT_KEY),
    "current_car_participation": "387",
}

QUESTION_TYPES = {
    RepField.NEW_CAR_PARTICIPATION: RepQuestionType.PARTICIPATION,
    RepField.OCCUPATION: RepQuestionType.OCCUPATION,
    RepField.ELIGIBILITY_GROUP: RepQuestionType.ELIGIBILITY_GROUP,
    RepField.CURRENT_CAR: RepQuestionType.OTHER,
    RepField.CURRENT_CAR_PARTICIPATION: RepQuestionType.OTHER,
}
EVERY_FIELD = pytest.mark.parametrize("field", list(RepField))


@pytest.fixture(autouse=True)
def employee_number(monkeypatch: pytest.MonkeyPatch) -> int:
    # A new conversation gets 1000 plus the draw.
    monkeypatch.setattr(secrets, "randbelow", lambda bound: EMPLOYEE_NUMBER - 1000)
    return EMPLOYEE_NUMBER


@pytest.fixture
def conversation_id(dataset_service: ConversationService) -> UUID:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, PROFILE)
    # The occupation is unlisted, so the group is the representative's Group_Override.
    set_group(dataset_service, conversation_id, "C")
    return conversation_id


def set_group(service: ConversationService, conversation_id: UUID, group: str) -> None:
    with service.transaction() as session:
        service.apply_rep_answer(session, conversation_id, {"eligibility_group": group})
        service.recalculate(session, conversation_id)


@pytest.fixture
def atto_3(app_config: AppConfig) -> CatalogCar:
    return catalog_car(app_config, ATTO_3_KEY)


def catalog_car(config: AppConfig, key: CarKey) -> CatalogCar:
    car = config.catalog.find(key)
    assert car is not None
    return car


def ask(
    service: ConversationService,
    sessions: sessionmaker[Session],
    conversation_id: UUID,
    field: RepField,
    car: CatalogCar | None = None,
) -> tuple[UUID, bool]:
    """Open or get a question in a transaction of its own, and return its ID and `created`."""
    with sessions.begin() as session:
        question, created = open_or_get(service, session, conversation_id, field, car)
        return question.id, created


def stored_questions(sessions: sessionmaker[Session]) -> list[RepQuestionRow]:
    with sessions() as session:
        rows = session.scalars(select(RepQuestionRow).order_by(RepQuestionRow.created_at)).all()
        session.expunge_all()
        return list(rows)


def stored_version(sessions: sessionmaker[Session], conversation_id: UUID) -> int:
    with sessions() as session:
        return session.get_one(ConversationRow, conversation_id).version


def comparison_of(service: ConversationService, conversation_id: UUID) -> ComparisonView:
    comparison = service.state(conversation_id).comparison
    assert comparison is not None
    return comparison


def sent_flags(missing: Iterable[MissingView]) -> dict[MissingField, bool]:
    return {item.field: item.sent_to_rep for item in missing}


def renderings(amount: Decimal) -> set[str]:
    return {format(value, spec) for value in (amount, amount.normalize()) for spec in ("f", ",f")}


def private_values(profile: EmployeeProfile, today: TodayView) -> set[str]:
    """Everything of the employee's pay that Transport_Rep must not read in a text."""
    components = profile.pay_components or []
    amounts = [
        profile.base_salary,
        *(component.amount for component in components),
        profile.pension_rate_percent,
        profile.study_fund_rate_percent,
        profile.credit_points,
        profile.current_car_participation,
    ]
    shown = [today.net_pay, today.usage_value, today.participation]
    description = profile.current_car
    assert description is not None
    values = {text for amount in amounts if amount is not None for text in renderings(amount)}
    values |= {text for n in shown if n is not None for text in (str(n), f"{n:,}")}
    values |= {component.label for component in components}
    values |= {description.manufacturer, description.model}
    return values


def test_a_second_call_returns_the_same_question_with_created_false(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    first = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION)
    second = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION)

    assert first[1] is True
    assert second == (first[0], False)
    assert len(stored_questions(sessions)) == 1


def test_a_second_call_in_the_same_transaction_returns_the_row_of_the_first(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    with sessions.begin() as session:
        first, created_first = open_or_get(
            dataset_service, session, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
        )
        second, created_second = open_or_get(
            dataset_service, session, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
        )

    assert (created_first, created_second) == (True, False)
    assert second is first
    assert len(stored_questions(sessions)) == 1


def test_each_combination_of_conversation_field_and_car_has_its_own_open_question(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    app_config: AppConfig,
    atto_3: CatalogCar,
) -> None:
    other = dataset_service.create()
    niro = catalog_car(app_config, NIRO_KEY)
    combinations = [
        (conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3),
        (conversation_id, RepField.NEW_CAR_PARTICIPATION, niro),
        (conversation_id, RepField.CURRENT_CAR_PARTICIPATION, None),
        (conversation_id, RepField.CURRENT_CAR, None),
        (other, RepField.NEW_CAR_PARTICIPATION, atto_3),
        (other, RepField.CURRENT_CAR, None),
    ]

    asked = [ask(dataset_service, sessions, *combination) for combination in combinations]

    assert all(created for _, created in asked)
    assert len({question_id for question_id, _ in asked}) == len(combinations)


def test_a_new_question_opens_once_the_first_is_answered(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    first_id, _ = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)
    with sessions.begin() as session:
        session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.id == first_id)
            .values(status=RepQuestionStatus.ANSWERED.value, answered_at=NOW)
        )

    second_id, created = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)

    assert created is True
    assert second_id != first_id


@EVERY_FIELD
def test_the_question_text_holds_no_profile_amount(
    field: RepField,
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    state = dataset_service.state(conversation_id)
    assert state.comparison is not None
    assert state.comparison.today.net_pay is not None
    private = private_values(state.profile, state.comparison.today)

    ask(dataset_service, sessions, conversation_id, field, atto_3)

    [view] = list_for_rep(sessions, SHIPPED)
    assert [value for value in private if value in view.question_text] == []


@pytest.mark.parametrize(
    ("field", "parts"),
    [
        pytest.param(
            RepField.NEW_CAR_PARTICIPATION,
            [DISPLAY_NAME, GROUP_C, "BYD Atto 3", str(ATTO_3_KEY)],
            id="new_car_participation",
        ),
        pytest.param(
            RepField.OCCUPATION, [DISPLAY_NAME, GROUP_C, UNLISTED_OCCUPATION], id="occupation"
        ),
        pytest.param(
            RepField.ELIGIBILITY_GROUP,
            [DISPLAY_NAME, GROUP_C, UNLISTED_OCCUPATION],
            id="eligibility_group",
        ),
        pytest.param(RepField.CURRENT_CAR, [DISPLAY_NAME, GROUP_C], id="current_car"),
        pytest.param(
            RepField.CURRENT_CAR_PARTICIPATION,
            [DISPLAY_NAME, GROUP_C],
            id="current_car_participation",
        ),
    ],
)
def test_the_question_text_names_the_employee_the_group_and_what_it_asks_about(
    field: RepField,
    parts: list[str],
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    ask(dataset_service, sessions, conversation_id, field, atto_3)

    [view] = list_for_rep(sessions, SHIPPED)
    assert [part for part in parts if part not in view.question_text] == []


def test_the_list_holds_the_open_questions_first_then_the_answered_ones_each_oldest_first(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    app_config: AppConfig,
    atto_3: CatalogCar,
) -> None:
    niro = catalog_car(app_config, NIRO_KEY)
    asked = {
        "a": ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)[0],
        "b": ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION)[0],
        "c": ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)[0],
        "d": ask(
            dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
        )[0],
        "e": ask(dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, niro)[
            0
        ],
    }
    # The creation times are not in the order the questions were asked.
    states = {
        "a": (RepQuestionStatus.OPEN, 3),
        "b": (RepQuestionStatus.ANSWERED, 1),
        "c": (RepQuestionStatus.OPEN, 2),
        "d": (RepQuestionStatus.ANSWERED, 0),
        "e": (RepQuestionStatus.OPEN, 4),
    }
    with sessions.begin() as session:
        for name, (status, minutes) in states.items():
            session.execute(
                update(RepQuestionRow)
                .where(RepQuestionRow.id == asked[name])
                .values(
                    status=status.value,
                    created_at=NOW + timedelta(minutes=minutes),
                    answered_at=None if status is RepQuestionStatus.OPEN else NOW,
                )
            )

    listed = [view.id for view in list_for_rep(sessions, SHIPPED)]

    assert listed == [asked[name] for name in "caedb"]


def test_the_employee_label_and_group_are_snapshots_taken_at_creation(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    first_id, _ = ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)
    renamed = "רונית כהן"
    dataset_service.update_profile(conversation_id, {"display_name": renamed})
    set_group(dataset_service, conversation_id, "A")
    second_id, _ = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)

    views = {view.id: view for view in list_for_rep(sessions, SHIPPED)}

    first, second = views[first_id], views[second_id]
    assert (first.employee.display_name, first.eligibility_group) == (DISPLAY_NAME, Group.C)
    assert DISPLAY_NAME in first.question_text
    assert renamed not in first.question_text
    assert (second.employee.display_name, second.eligibility_group) == (renamed, Group.A)
    assert renamed in second.question_text
    snapshot = {row.id: row for row in stored_questions(sessions)}[first_id]
    assert (snapshot.employee_display_name, snapshot.eligibility_group) == (DISPLAY_NAME, "C")


def test_without_a_display_name_the_label_is_built_from_the_employee_number(
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draws = iter([3821, 4521])
    monkeypatch.setattr(secrets, "randbelow", lambda bound: next(draws))
    first, second = dataset_service.create(), dataset_service.create()
    conversations = {first: 4821, second: 5521}
    asked: dict[UUID, UUID] = {}
    for conversation in conversations:
        dataset_service.update_profile(conversation, {"occupation": UNLISTED_OCCUPATION})
        asked[conversation] = ask(dataset_service, sessions, conversation, RepField.OCCUPATION)[0]

    views = {view.id: view for view in list_for_rep(sessions, SHIPPED)}

    for conversation, number in conversations.items():
        view = views[asked[conversation]]
        label = copy_he.employee_label(number)
        assert (view.employee.display_name, view.employee.number) == (label, number)
        # Without a group, the text starts with the label alone.
        assert view.question_text.startswith(f"{label}:")
        assert str(conversation) not in view.question_text + view.employee.display_name
        assert view.eligibility_group is None


def test_creating_a_question_marks_only_the_car_it_is_about_as_awaiting_the_rep(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3, NIRO])
    version = stored_version(sessions, conversation_id)

    def marks() -> list[tuple[str, object, bool]]:
        columns = comparison_of(dataset_service, conversation_id).alternatives
        return [
            (column.status, column.participation_source, column.participation_awaiting_rep)
            for column in columns
        ]

    assert marks() == [("calculated", "catalog", False), ("calculated", "catalog", False)]

    ask(dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3)

    # The catalog amount stays in use until the representative answers (Req 6.23).
    assert marks() == [("calculated", "catalog", True), ("calculated", "catalog", False)]
    assert stored_version(sessions, conversation_id) == version + 1


def test_a_question_about_the_employee_is_marked_sent_in_today_and_in_the_waiver_column(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    dataset_service.update_profile(conversation_id, {"current_car_participation": None})
    dataset_service.set_alternatives(conversation_id, [WAIVER])
    occupation = MissingField.OCCUPATION
    participation = MissingField.CURRENT_CAR_PARTICIPATION
    before = comparison_of(dataset_service, conversation_id)
    assert sent_flags(before.today.missing) == {participation: False}
    assert sent_flags(before.alternatives[0].missing) == {occupation: False, participation: False}

    ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION)
    ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)

    after = comparison_of(dataset_service, conversation_id)
    assert sent_flags(after.today.missing) == {participation: True}
    assert sent_flags(after.alternatives[0].missing) == {occupation: True, participation: True}


def test_returning_an_open_question_changes_nothing(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)
    version = stored_version(sessions, conversation_id)
    comparison = comparison_of(dataset_service, conversation_id)

    ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)

    assert stored_version(sessions, conversation_id) == version
    assert comparison_of(dataset_service, conversation_id) == comparison


def test_a_conversation_edited_in_the_meantime_rolls_the_new_question_back(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    dataset_service.set_alternatives(conversation_id, [ATTO_3])
    version = stored_version(sessions, conversation_id)
    comparison = comparison_of(dataset_service, conversation_id)

    with pytest.raises(StaleDataError), sessions.begin() as session:
        open_or_get(
            dataset_service, session, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
        )
        # Another request edits the conversation after this one has read it.
        session.connection().execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(version=ConversationRow.version + 1)
        )

    assert stored_questions(sessions) == []
    assert stored_version(sessions, conversation_id) == version
    assert comparison_of(dataset_service, conversation_id) == comparison


def test_the_car_is_stored_with_a_participation_question_and_with_no_other(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    participation_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    # The engine names no car for a field of the employee, so a question about one has no car.
    with_car = ask(
        dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION, atto_3
    )
    without_car = ask(
        dataset_service, sessions, conversation_id, RepField.CURRENT_CAR_PARTICIPATION
    )

    rows = {row.id: row for row in stored_questions(sessions)}
    assert without_car == (with_car[0], False)
    assert (rows[participation_id].car_key, rows[participation_id].car_display_name) == (
        str(ATTO_3_KEY),
        "BYD Atto 3",
    )
    assert (rows[with_car[0]].car_key, rows[with_car[0]].car_display_name) == (NO_CAR, None)


@pytest.mark.parametrize(
    ("field", "change", "error"),
    [
        pytest.param(RepField.NEW_CAR_PARTICIPATION, {}, "needs a car", id="participation-no-car"),
        pytest.param(RepField.OCCUPATION, {"occupation": None}, "occupation", id="no-occupation"),
        pytest.param(RepField.OCCUPATION, {"occupation": "  "}, "occupation", id="blank"),
    ],
)
def test_a_question_without_what_its_text_needs_is_refused_and_stores_nothing(
    field: RepField,
    change: dict[str, object],
    error: str,
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
) -> None:
    dataset_service.update_profile(conversation_id, change)
    version = stored_version(sessions, conversation_id)

    with pytest.raises(ValueError, match=error):
        ask(dataset_service, sessions, conversation_id, field)

    assert stored_questions(sessions) == []
    assert stored_version(sessions, conversation_id) == version


def test_an_unknown_conversation_raises_not_found_and_stores_nothing(
    dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    with pytest.raises(ConversationNotFound):
        ask(dataset_service, sessions, uuid4(), RepField.CURRENT_CAR)

    assert stored_questions(sessions) == []


def test_text_typed_by_the_employee_is_clipped_to_fit_its_columns(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    dataset_service.update_profile(
        conversation_id, {"display_name": "א" * 500, "occupation": "ב" * 500}
    )

    ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)

    [row] = stored_questions(sessions)
    assert row.employee_display_name is not None
    assert len(row.employee_display_name) <= 100
    assert len(row.question_text) <= 1000


@pytest.mark.parametrize("field", QUESTION_TYPES)
def test_a_question_has_the_type_of_its_answer_and_a_field_of_the_engine(
    field: RepField,
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    ask(dataset_service, sessions, conversation_id, field, atto_3)

    [row] = stored_questions(sessions)
    assert row.question_type == QUESTION_TYPES[field].value
    assert row.missing_field == MissingField(field.value).value
    assert row.status == RepQuestionStatus.OPEN.value
    assert (row.employee_number, row.eligibility_group) == (EMPLOYEE_NUMBER, "C")


def test_a_view_holds_the_fields_of_the_design_and_never_the_conversation_id(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    question_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )

    [view] = list_for_rep(sessions, SHIPPED)

    assert set(RepQuestionView.model_fields) == {
        "id",
        "question_type",
        "question_text",
        "created_at",
        "employee",
        "eligibility_group",
        "car",
        "suggested_amount",
        "choices",
        "status",
        "answer",
    }
    assert view.id == question_id
    assert (view.suggested_amount, view.choices) == (atto_3.default_participation, None)
    assert view.question_type is RepQuestionType.PARTICIPATION
    assert (view.status, view.answer) == (RepQuestionStatus.OPEN, None)
    assert (view.employee.display_name, view.employee.number) == (DISPLAY_NAME, EMPLOYEE_NUMBER)
    assert view.eligibility_group is Group.C
    assert view.car == RepCar(display_name="BYD Atto 3", car_key=ATTO_3_KEY)
    assert view.created_at.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - view.created_at) < timedelta(minutes=1)
    serialized = view.model_dump_json()
    assert str(conversation_id) not in serialized
    assert conversation_id.hex not in serialized


def test_an_answered_question_comes_back_with_its_stored_answer(
    conversation_id: UUID,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    atto_3: CatalogCar,
) -> None:
    participation_id, _ = ask(
        dataset_service, sessions, conversation_id, RepField.NEW_CAR_PARTICIPATION, atto_3
    )
    text_id, _ = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)
    choice_id, _ = ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)
    answered = {"status": RepQuestionStatus.ANSWERED.value, "answered_at": NOW}
    with sessions.begin() as session:
        session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.id == participation_id)
            .values(
                **answered,
                participation_amount=Decimal("350.50"),
                participation_effective_date=date(2026, 1, 1),
                answer_note="Starts in January",
            )
        )
        session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.id == text_id)
            .values(**answered, answer_text="קיה פיקנטו 2023")
        )
        session.execute(
            update(RepQuestionRow)
            .where(RepQuestionRow.id == choice_id)
            .values(**answered, answer_choice="מנהל/ת מוצר")
        )

    views = {view.id: view for view in list_for_rep(sessions, SHIPPED)}

    assert views[participation_id].status is RepQuestionStatus.ANSWERED
    assert views[participation_id].answer == StoredAnswer(
        amount=Decimal("350.50"),
        effective_date=date(2026, 1, 1),
        note="Starts in January",
        text=None,
    )
    assert views[text_id].answer == StoredAnswer(
        amount=None, effective_date=None, note=None, text="קיה פיקנטו 2023"
    )
    assert views[choice_id].answer == StoredAnswer(
        amount=None, effective_date=None, note=None, text=None, choice="מנהל/ת מוצר"
    )


def test_a_choice_question_lists_its_choices_and_no_catalog_amount(
    conversation_id: UUID, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    occupation_id, _ = ask(dataset_service, sessions, conversation_id, RepField.OCCUPATION)
    group_id, _ = ask(dataset_service, sessions, conversation_id, RepField.ELIGIBILITY_GROUP)
    other_id, _ = ask(dataset_service, sessions, conversation_id, RepField.CURRENT_CAR)

    views = {view.id: view for view in list_for_rep(sessions, SHIPPED)}

    occupation, group, other = views[occupation_id], views[group_id], views[other_id]
    assert occupation.choices is not None
    assert [choice.value for choice in occupation.choices] == [
        item.name for item in SHIPPED.occupations.occupations
    ]
    assert group.choices is not None
    assert [(choice.value, choice.label) for choice in group.choices] == [
        ("A", "א"),
        ("B", "ב"),
        ("C", "ג"),
        ("D", "ד"),
    ]
    assert other.choices is None
    assert {view.suggested_amount for view in (occupation, group, other)} == {None}
