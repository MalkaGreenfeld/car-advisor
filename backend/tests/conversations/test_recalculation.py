import itertools
import json
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import car_advisor.conversations.service as service_module
from car_advisor.conversations.models import NO_CAR, ConversationRow, RepQuestionRow
from car_advisor.conversations.profile import FieldError, ProfileValidationError
from car_advisor.conversations.service import (
    AlternativeNotFound,
    CarView,
    ComparisonView,
    ConversationConflict,
    ConversationNotFound,
    ConversationService,
    ConversationState,
    MissingView,
)
from car_advisor.dataset.lookup import CarLookup
from car_advisor.engine import copy_he
from car_advisor.engine.comparison import Comparison, ComparisonInputs, compare
from car_advisor.engine.types import (
    Alternative,
    CarAlternative,
    CarKey,
    CarRecord,
    EmployeeProfile,
    MissingField,
    RepParticipation,
    WaiverAlternative,
)
from car_advisor.rules.loader import AppConfig
from tests.conversations.conftest import without_default_participation

D = Decimal
NOW = datetime(2026, 3, 1, 9, 30)

# Cars of the real dataset, and what the mock catalog says about them.
CURRENT_KEY = CarKey.parse("2026-2023-1-01229-00177")  # a 2023 Tesla Model 3, not in the catalog
ATTO_3_KEY = CarKey.parse("2026-2025-1-01014-00003")  # group C, costs 4,100, electric
NIRO_KEY = CarKey.parse("2026-2025-1-00885-00619")  # group B, costs 3,600, hybrid
PICANTO_KEY = CarKey.parse("2026-2025-1-00885-00643")  # group A, costs 2,650, conventional
SEAL_KEY = CarKey.parse("2026-2025-1-01014-00014")  # group D, costs 5,200, electric
ALL_KEYS = (CURRENT_KEY, ATTO_3_KEY, NIRO_KEY, PICANTO_KEY, SEAL_KEY)

ATTO_3 = CarAlternative(car_key=ATTO_3_KEY)
NIRO = CarAlternative(car_key=NIRO_KEY)
PICANTO = CarAlternative(car_key=PICANTO_KEY)
SEAL = CarAlternative(car_key=SEAL_KEY)
CARNIVAL_KEY = CarKey.parse("2026-2025-1-00885-00648")  # a family-track car, costs 6,000
CARNIVAL = CarAlternative(car_key=CARNIVAL_KEY)
WAIVER = WaiverAlternative()

# A product manager, so group C, who drives the Tesla and pays 350 for it.
PRODUCT_MANAGER = "מנהל/ת מוצר"
EMPLOYEE: dict[str, object] = {
    "occupation": PRODUCT_MANAGER,
    "base_salary": "16000",
    "pay_components": [{"kind": "bonus", "label": "Annual", "amount": "500"}],
    "pension_rate_percent": "6",
    "study_fund_rate_percent": "2.5",
    "credit_points": "2.25",
    "children_under_23": 0,
    "has_current_car": True,
    "current_car_key": str(CURRENT_KEY),
    "current_car_participation": "350",
}
ANSWER_400 = RepParticipation(amount=D(400), effective_date=date(2026, 1, 1))

# A constant, so the call line that a traceback prints does not hold the value itself.
REJECTED_SALARY = "-98765"

TODAY_KEYS = {
    "status",
    "car",
    "participation",
    "usage_value",
    "ev_refund",
    "car_maintenance",
    "net_pay",
    "missing",
}
COLUMN_KEYS = {
    "position",
    "status",
    "car",
    "eligibility",
    "car_cost",
    "car_budget",
    "participation",
    "participation_effective_date",
    "participation_source",
    "participation_awaiting_rep",
    "employer_gap_share",
    "employee_gap_share",
    "usage_value",
    "ev_refund",
    "car_maintenance",
    "unused_budget",
    "net_pay",
    "difference",
    "breakdown",
    "explanation",
    "missing",
}


@pytest.fixture
def lookup(real_sessions: sessionmaker[Session]) -> CarLookup:
    return CarLookup(real_sessions, 2026)


class EngineView:
    """What the engine returns for a stored state, built here and not by the service."""

    def __init__(self, config: AppConfig, lookup: CarLookup) -> None:
        self._config = config
        self._lookup = lookup

    def __call__(
        self,
        state: ConversationState,
        answers: Mapping[CarKey, RepParticipation] | None = None,
        sent: frozenset[tuple[MissingField, CarKey | None]] = frozenset(),
    ) -> ComparisonView:
        # Every car of these tests is loaded, whether the state uses it or not.
        inputs = ComparisonInputs(
            rules=self._config.rules,
            catalog=self._config.catalog,
            occupations=self._config.occupations,
            records=self._lookup.get_many(ALL_KEYS),
            rep_participations={} if answers is None else answers,
            rep_questions=sent,
        )
        comparison = compare(state.profile, state.alternatives, inputs)
        return ComparisonView.from_comparison(comparison, inputs)


@pytest.fixture
def engine_view(app_config: AppConfig, lookup: CarLookup) -> EngineView:
    return EngineView(app_config, lookup)


def stored_version(sessions: sessionmaker[Session], conversation_id: UUID) -> int:
    with sessions() as session:
        return session.get_one(ConversationRow, conversation_id).version


def add_question(
    sessions: sessionmaker[Session],
    conversation_id: UUID,
    field: MissingField,
    car_key: CarKey | None = None,
    *,
    status: str = "open",
    answer: RepParticipation | None = None,
    answered_at: datetime = NOW,
) -> None:
    with sessions.begin() as session:
        session.add(
            RepQuestionRow(
                conversation_id=conversation_id,
                question_type="participation" if answer is not None else "other",
                missing_field=field.value,
                car_key=NO_CAR if car_key is None else str(car_key),
                question_text="A question for the representative.",
                employee_number=4821,
                status=status,
                created_at=NOW,
                answered_at=answered_at if status == "answered" else None,
                participation_amount=None if answer is None else answer.amount,
                participation_effective_date=None if answer is None else answer.effective_date,
            )
        )


def recalculate(
    service: ConversationService, sessions: sessionmaker[Session], conversation_id: UUID
) -> ComparisonView:
    with sessions.begin() as session:
        return service.recalculate(session, conversation_id)


def comparison_of(state: ConversationState) -> ComparisonView:
    assert state.comparison is not None
    return state.comparison


def test_update_profile_stores_nothing_when_one_field_is_invalid(
    dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    before = dataset_service.state(conversation_id)
    version = stored_version(sessions, conversation_id)

    with pytest.raises(ProfileValidationError) as caught:
        dataset_service.update_profile(
            conversation_id,
            {"base_salary": "20000", "eligibility_group": "B", "pension_rate_percent": "101"},
        )

    ranges = copy_he.PROFILE_FIELD_RANGES
    assert caught.value.errors == (
        FieldError("eligibility_group", copy_he.GROUP_FROM_OCCUPATION),
        FieldError("pension_rate_percent", ranges["pension_rate_percent"]),
    )
    assert dataset_service.state(conversation_id) == before
    assert stored_version(sessions, conversation_id) == version


def test_update_profile_returns_the_stored_profile_and_a_none_value_clears_its_field(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()

    profile = dataset_service.update_profile(conversation_id, EMPLOYEE)
    cleared = dataset_service.update_profile(conversation_id, {"current_car_participation": None})
    no_components = dataset_service.update_profile(conversation_id, {"pay_components": []})

    assert profile == EmployeeProfile.model_validate(EMPLOYEE)
    assert cleared == profile.model_copy(update={"current_car_participation": None})
    # An empty list is an answer, and stays one: only None means "not provided".
    assert no_components == cleared.model_copy(update={"pay_components": []})
    assert dataset_service.state(conversation_id).profile == no_components


def test_update_profile_partial_stores_the_valid_fields_and_returns_the_rejected_ones(
    dataset_service: ConversationService, engine_view: EngineView
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    before = dataset_service.state(conversation_id)

    update = dataset_service.update_profile_partial(
        conversation_id,
        {
            "credit_points": "3",
            "pension_rate_percent": "101",
            "base_salary": "20000",
            "eligibility_group": "B",
        },
    )

    ranges = copy_he.PROFILE_FIELD_RANGES
    stored = before.profile.model_copy(update={"base_salary": D(20000), "credit_points": D(3)})
    assert update.saved == ("base_salary", "credit_points")
    assert update.rejected == (
        FieldError("eligibility_group", copy_he.GROUP_FROM_OCCUPATION),
        FieldError("pension_rate_percent", ranges["pension_rate_percent"]),
    )
    assert update.profile == stored
    after = dataset_service.state(conversation_id)
    assert after.profile == stored
    assert after.comparison == engine_view(after)


def test_update_profile_partial_with_only_invalid_values_leaves_the_profile_as_it_was(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    before = dataset_service.state(conversation_id)

    update = dataset_service.update_profile_partial(conversation_id, {"credit_points": "-1"})

    assert update.saved == ()
    assert [error.field for error in update.rejected] == ["credit_points"]
    assert dataset_service.state(conversation_id) == before


def test_a_name_that_is_not_a_profile_field_is_rejected_on_both_update_paths(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()
    unknown = FieldError("salary", copy_he.UNKNOWN_PROFILE_FIELD)

    with pytest.raises(ProfileValidationError) as caught:
        dataset_service.update_profile(conversation_id, {"salary": "16000", "base_salary": "1"})
    update = dataset_service.update_profile_partial(
        conversation_id, {"salary": "16000", "base_salary": "1"}
    )

    assert caught.value.errors == (unknown,)
    assert update.saved == ("base_salary",)
    assert update.rejected == (unknown,)
    assert dataset_service.state(conversation_id).profile.base_salary == D(1)


def test_a_rejected_value_never_reaches_the_error_text(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()

    with pytest.raises(ProfileValidationError) as caught:
        dataset_service.update_profile(conversation_id, {"base_salary": REJECTED_SALARY})

    assert "base_salary" in str(caught.value)
    assert REJECTED_SALARY.strip("-") not in "".join(traceback.format_exception(caught.value))


def test_after_each_mutation_the_stored_comparison_is_what_the_engine_returns_for_the_stored_state(
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    app_config: AppConfig,
    engine_view: EngineView,
) -> None:
    service = dataset_service
    conversation_id = service.create()
    waiver_occupation = next(
        item.name for item in app_config.occupations.occupations if item.car_waiver_allowed
    )
    answers: dict[CarKey, RepParticipation] = {}
    sent: set[tuple[MissingField, CarKey | None]] = set()

    def representative_answers() -> None:
        add_question(
            sessions,
            conversation_id,
            MissingField.NEW_CAR_PARTICIPATION,
            ATTO_3_KEY,
            status="answered",
            answer=ANSWER_400,
        )
        answers[ATTO_3_KEY] = ANSWER_400
        sent.add((MissingField.NEW_CAR_PARTICIPATION, ATTO_3_KEY))
        recalculate(service, sessions, conversation_id)

    steps: list[tuple[str, Callable[[], object]]] = [
        ("panel edit", lambda: service.update_profile(conversation_id, EMPLOYEE)),
        (
            "alternatives",
            lambda: service.set_alternatives(conversation_id, [ATTO_3, NIRO, SEAL, WAIVER]),
        ),
        ("rep answer", representative_answers),
        (
            "agent edit",
            lambda: service.update_profile_partial(
                conversation_id, {"occupation": "מפתח/ת תוכנה", "base_salary": "-1"}
            ),
        ),
        ("car picker", lambda: service.replace_alternative(conversation_id, 1, PICANTO)),
        ("no car", lambda: service.update_profile(conversation_id, {"has_current_car": False})),
        (
            "occupation",
            lambda: service.update_profile(conversation_id, {"occupation": waiver_occupation}),
        ),
    ]
    for name, step in steps:
        step()

        state = service.state(conversation_id)
        assert state.comparison == engine_view(state, answers, frozenset(sent)), name


def test_set_alternatives_returns_the_comparison_it_stored(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)

    returned = dataset_service.set_alternatives(conversation_id, [ATTO_3, WAIVER])

    state = dataset_service.state(conversation_id)
    assert state.alternatives == (ATTO_3, WAIVER)
    assert returned == state.comparison
    assert [column.position for column in returned.alternatives] == [0, 1]


def test_replace_alternative_replaces_one_column_and_returns_the_new_state(
    dataset_service: ConversationService, engine_view: EngineView
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    dataset_service.set_alternatives(conversation_id, [ATTO_3, NIRO])

    state = dataset_service.replace_alternative(conversation_id, 1, PICANTO)

    assert state == dataset_service.state(conversation_id)
    assert state.alternatives == (ATTO_3, PICANTO)
    assert state.comparison == engine_view(state)
    cars = [column.car for column in comparison_of(state).alternatives]
    assert [car.car_key for car in cars if car is not None] == [ATTO_3_KEY, PICANTO_KEY]


@pytest.mark.parametrize("position", [-1, 2, 7])
def test_replace_alternative_rejects_a_position_without_a_column_and_stores_nothing(
    position: int, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.set_alternatives(conversation_id, [ATTO_3, WAIVER])
    before = dataset_service.state(conversation_id)
    version = stored_version(sessions, conversation_id)

    with pytest.raises(AlternativeNotFound):
        dataset_service.replace_alternative(conversation_id, position, PICANTO)

    assert dataset_service.state(conversation_id) == before
    assert stored_version(sessions, conversation_id) == version


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s, i: s.update_profile(i, {}), id="update_profile"),
        pytest.param(lambda s, i: s.update_profile_partial(i, {}), id="update_profile_partial"),
        pytest.param(lambda s, i: s.set_alternatives(i, [WAIVER]), id="set_alternatives"),
        pytest.param(lambda s, i: s.replace_alternative(i, 0, WAIVER), id="replace_alternative"),
    ],
)
def test_every_mutation_of_an_unknown_conversation_raises_not_found_and_creates_nothing(
    mutate: Callable[[ConversationService, UUID], object],
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
) -> None:
    with pytest.raises(ConversationNotFound):
        mutate(dataset_service, uuid4())

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ConversationRow)) == 0


class InterferingLookup(CarLookup):
    """A lookup that lets another request edit the conversation while a change is being made.

    A change has read its conversation when it looks up the cars and has not written yet, which
    is the moment a concurrent edit makes it stale.
    """

    def __init__(self, sessions: sessionmaker[Session], tax_year: int) -> None:
        super().__init__(sessions, tax_year)
        self.interfere: Callable[[], object] | None = None
        self.attempts = 0
        self._interfering = False

    def get_many(self, keys: Iterable[CarKey]) -> dict[CarKey, CarRecord]:
        # The edit made by the other request looks the cars up as well, and is not counted.
        if self.interfere is not None and not self._interfering:
            self.attempts += 1
            self._interfering = True
            try:
                self.interfere()
            finally:
                self._interfering = False
        return super().get_many(keys)


@pytest.fixture
def racing(
    sessions: sessionmaker[Session], real_sessions: sessionmaker[Session], app_config: AppConfig
) -> tuple[ConversationService, InterferingLookup]:
    lookup = InterferingLookup(real_sessions, 2026)
    return ConversationService(sessions, lookup, app_config), lookup


def test_a_concurrent_version_bump_is_retried_once_on_the_fresh_data(
    racing: tuple[ConversationService, InterferingLookup], engine_view: EngineView
) -> None:
    service, lookup = racing
    conversation_id = service.create()
    service.update_profile(conversation_id, {"base_salary": "16000"})
    salaries = iter(["17000"])

    def other_request() -> None:
        salary = next(salaries, None)
        if salary is not None:
            service.update_profile(conversation_id, {"base_salary": salary})

    lookup.interfere = other_request

    profile = service.update_profile(conversation_id, {"credit_points": "2"})

    assert lookup.attempts == 2
    assert (profile.base_salary, profile.credit_points) == (D(17000), D(2))
    state = service.state(conversation_id)
    assert state.profile == profile
    assert state.comparison == engine_view(state)


def test_an_answer_that_lands_while_a_change_is_calculated_reaches_the_stored_comparison(
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
    engine_view: EngineView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    landed: list[bool] = []

    def compare_while_an_answer_lands(
        profile: EmployeeProfile, alternatives: Sequence[Alternative], inputs: ComparisonInputs
    ) -> Comparison:
        # The change has read the answers when this one lands. The car is not a column yet, so
        # the answer alone leaves the stored comparison as it was, and only the version check
        # can make the change calculate again with the answer.
        if not landed:
            landed.append(True)
            add_question(
                sessions,
                conversation_id,
                MissingField.NEW_CAR_PARTICIPATION,
                ATTO_3_KEY,
                status="answered",
                answer=ANSWER_400,
            )
            recalculate(dataset_service, sessions, conversation_id)
        return compare(profile, alternatives, inputs)

    monkeypatch.setattr(service_module, "compare", compare_while_an_answer_lands)

    returned = dataset_service.set_alternatives(conversation_id, [ATTO_3])

    state = dataset_service.state(conversation_id)
    answers = {ATTO_3_KEY: ANSWER_400}
    sent = frozenset({(MissingField.NEW_CAR_PARTICIPATION, ATTO_3_KEY)})
    assert state.comparison == returned == engine_view(state, answers, sent)
    assert returned.alternatives[0].status == "calculated"


def test_a_change_that_goes_stale_twice_raises_a_conflict_and_stores_nothing(
    racing: tuple[ConversationService, InterferingLookup], engine_view: EngineView
) -> None:
    service, lookup = racing
    conversation_id = service.create()
    salaries = itertools.count(17000)

    def other_request() -> None:
        service.update_profile(conversation_id, {"base_salary": str(next(salaries))})

    lookup.interfere = other_request

    with pytest.raises(ConversationConflict) as caught:
        service.update_profile(conversation_id, {"credit_points": "2"})

    assert lookup.attempts == 2
    assert "17000" not in str(caught.value)
    state = service.state(conversation_id)
    assert state.profile == EmployeeProfile(base_salary=D(17001))
    assert state.comparison == engine_view(state)


@pytest.fixture
def showcase(
    sessions: sessionmaker[Session], real_sessions: sessionmaker[Session], app_config: AppConfig
) -> dict[str, Any]:
    """The stored comparison as JSON: one column of each status, and one for giving up the car.

    The Niro has no catalog participation here, so its column waits for the representative.
    """
    config = without_default_participation(app_config, NIRO_KEY)
    dataset_service = ConversationService(sessions, CarLookup(real_sessions, 2026), config)
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    add_question(
        sessions,
        conversation_id,
        MissingField.NEW_CAR_PARTICIPATION,
        ATTO_3_KEY,
        status="answered",
        answer=ANSWER_400,
    )
    add_question(sessions, conversation_id, MissingField.NEW_CAR_PARTICIPATION, NIRO_KEY)
    dataset_service.set_alternatives(conversation_id, [ATTO_3, NIRO, SEAL, WAIVER])

    stored = comparison_of(dataset_service.state(conversation_id))
    return json.loads(stored.model_dump_json())


def amounts(column: Mapping[str, Any], *names: str) -> list[object]:
    return [column[name] for name in names]


def test_the_payload_has_the_keys_and_the_column_order_of_the_design(
    showcase: dict[str, Any],
) -> None:
    assert set(showcase) == {"today", "alternatives"}
    assert set(showcase["today"]) == TODAY_KEYS
    columns = showcase["alternatives"]
    assert all(set(column) == COLUMN_KEYS for column in columns)
    assert [(column["position"], column["status"]) for column in columns] == [
        (0, "calculated"),
        (1, "pending"),
        (2, "not_eligible"),
        (3, "calculated"),
    ]


def test_a_calculated_column_carries_whole_shekels_and_text(
    showcase: dict[str, Any], lookup: CarLookup, app_config: AppConfig
) -> None:
    today, column = showcase["today"], showcase["alternatives"][0]
    catalog_car = app_config.catalog.find(ATTO_3_KEY)
    assert catalog_car is not None
    record = lookup.get_many([ATTO_3_KEY])[ATTO_3_KEY]

    assert column["car"] == {
        "car_key": str(ATTO_3_KEY),
        "display_name": catalog_car.display_name,
        "group": "C",
    }
    assert column["eligibility"]["status"] == "eligible"
    assert column["eligibility"]["is_electric"] is True
    assert column["eligibility"]["reason"]
    assert amounts(column, "car_cost", "car_budget", "unused_budget") == [4100, 4500, 400]
    assert amounts(column, "participation", "participation_effective_date") == [400, "2026-01-01"]
    assert amounts(column, "participation_source", "participation_awaiting_rep") == [
        "representative",
        False,
    ]
    assert column["employer_gap_share"] is None
    assert amounts(column, "usage_value", "ev_refund", "car_maintenance") == [
        record.usage_value,
        250,
        0,
    ]
    assert column["missing"] == []
    whole = amounts(column, "net_pay", "difference", *column["breakdown"])
    assert all(type(value) is int for value in whole)
    assert column["difference"] == column["net_pay"] - today["net_pay"]
    assert sum(column["breakdown"].values()) == column["difference"]
    assert column["explanation"]
    assert all(isinstance(line, str) and line for line in column["explanation"])


def test_a_pending_column_keeps_what_is_known_and_lists_the_missing_field_with_its_label(
    showcase: dict[str, Any], lookup: CarLookup
) -> None:
    column = showcase["alternatives"][1]
    record = lookup.get_many([NIRO_KEY])[NIRO_KEY]

    assert column["missing"] == [
        {
            "field": "new_car_participation",
            "label": copy_he.MISSING_FIELD_LABELS[MissingField.NEW_CAR_PARTICIPATION],
            "sent_to_rep": True,
        }
    ]
    assert column["eligibility"]["status"] == "eligible"
    assert amounts(column, "car_cost", "car_budget", "usage_value") == [
        3600,
        4500,
        record.usage_value,
    ]
    for withheld in ("participation", "net_pay", "difference", "breakdown", "explanation"):
        assert column[withheld] is None


def test_a_not_eligible_column_carries_only_the_car_the_status_and_the_reason(
    showcase: dict[str, Any],
) -> None:
    column = showcase["alternatives"][2]

    assert column["car"]["car_key"] == str(SEAL_KEY)
    assert column["eligibility"]["status"] == "not_eligible"
    assert column["eligibility"]["reason"]
    assert column["missing"] == []
    carried = {"position", "status", "car", "eligibility", "missing"}
    assert column["participation_awaiting_rep"] is False
    shown = COLUMN_KEYS - carried - {"participation_awaiting_rep"}
    assert [name for name in shown if column[name] is not None] == []


def test_giving_up_the_car_is_a_column_without_a_car_that_pays_the_maintenance(
    showcase: dict[str, Any], app_config: AppConfig
) -> None:
    column = showcase["alternatives"][3]
    entry = app_config.occupations.find(PRODUCT_MANAGER)
    assert entry is not None
    maintenance = entry.car_maintenance

    assert column["car"] is None
    assert amounts(column, "usage_value", "participation", "ev_refund") == [0, 0, 0]
    assert column["car_maintenance"] == maintenance
    assert all(column[name] is None for name in ("car_cost", "car_budget", "unused_budget"))


def test_today_names_a_current_car_that_is_outside_the_catalog_from_its_record(
    showcase: dict[str, Any], lookup: CarLookup
) -> None:
    today = showcase["today"]
    record = lookup.get_many([CURRENT_KEY])[CURRENT_KEY]

    assert today["status"] == "calculated"
    assert today["car"] == {
        "car_key": str(CURRENT_KEY),
        "display_name": f"{record.manufacturer_name} {record.trade_name}",
        "group": None,
    }
    assert amounts(today, "participation", "usage_value", "ev_refund", "car_maintenance") == [
        350,
        record.usage_value,
        250,
        0,
    ]
    assert type(today["net_pay"]) is int
    assert today["missing"] == []


def test_a_current_car_without_a_key_is_valid_and_leaves_today_pending(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()
    description = {"manufacturer": "Tesla", "model": "Model 3", "registration_year": 2023}
    dataset_service.update_profile(
        conversation_id,
        {**EMPLOYEE, "current_car_key": None, "current_car": description},
    )

    view = dataset_service.set_alternatives(conversation_id, [ATTO_3])

    assert view.today.status == "pending"
    assert view.today.car is None
    assert view.today.net_pay is None
    assert [item.field for item in view.today.missing] == [MissingField.CURRENT_CAR]
    assert view.alternatives[0].status == "pending"
    assert MissingField.CURRENT_CAR in [item.field for item in view.alternatives[0].missing]


def test_a_car_that_is_in_neither_the_catalog_nor_the_dataset_is_a_pending_column(
    dataset_service: ConversationService,
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    unknown = CarAlternative(car_key=CarKey.parse("2026-2026-1-99999-99999"))

    [column] = dataset_service.set_alternatives(conversation_id, [unknown]).alternatives

    assert column.status == "pending"
    assert column.car == CarView(car_key=unknown.car_key, display_name=None, group=None)
    assert [item.field for item in column.missing] == [
        MissingField.CAR_GROUP,
        MissingField.POWERTRAIN,
    ]


def test_a_confirmation_question_marks_only_its_car_as_awaiting_and_keeps_the_catalog_amount(
    dataset_service: ConversationService, sessions: sessionmaker[Session], app_config: AppConfig
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    add_question(sessions, conversation_id, MissingField.NEW_CAR_PARTICIPATION, PICANTO_KEY)

    picanto, niro = dataset_service.set_alternatives(conversation_id, [PICANTO, NIRO]).alternatives

    catalog = app_config.catalog
    for column, key, awaiting in ((picanto, PICANTO_KEY, True), (niro, NIRO_KEY, False)):
        car = catalog.find(key)
        assert car is not None
        assert column.status == "calculated"
        assert column.participation == car.default_participation
        assert (column.participation_source, column.participation_awaiting_rep) == (
            "catalog",
            awaiting,
        )
        assert column.missing == ()


def test_a_question_about_a_car_marks_only_the_column_of_that_car_as_sent(
    sessions: sessionmaker[Session], real_sessions: sessionmaker[Session], app_config: AppConfig
) -> None:
    # Without catalog participations both cars wait for the representative.
    config = without_default_participation(app_config, PICANTO_KEY, NIRO_KEY)
    dataset_service = ConversationService(sessions, CarLookup(real_sessions, 2026), config)
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    add_question(sessions, conversation_id, MissingField.NEW_CAR_PARTICIPATION, PICANTO_KEY)

    view = dataset_service.set_alternatives(conversation_id, [PICANTO, NIRO])

    label = copy_he.MISSING_FIELD_LABELS[MissingField.NEW_CAR_PARTICIPATION]
    picanto, niro = view.alternatives
    assert picanto.missing == (
        MissingView(field=MissingField.NEW_CAR_PARTICIPATION, label=label, sent_to_rep=True),
    )
    assert niro.missing == (
        MissingView(field=MissingField.NEW_CAR_PARTICIPATION, label=label, sent_to_rep=False),
    )


@pytest.mark.parametrize("row_car", [None, PICANTO_KEY], ids=["no-car", "stray-car"])
def test_a_question_about_the_employee_is_marked_sent_whatever_car_its_row_holds(
    row_car: CarKey | None,
    dataset_service: ConversationService,
    sessions: sessionmaker[Session],
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, {**EMPLOYEE, "current_car_participation": None})
    add_question(sessions, conversation_id, MissingField.CURRENT_CAR_PARTICIPATION, row_car)

    view = dataset_service.set_alternatives(conversation_id, [])

    assert view.today.missing == (
        MissingView(
            field=MissingField.CURRENT_CAR_PARTICIPATION,
            label=copy_he.MISSING_FIELD_LABELS[MissingField.CURRENT_CAR_PARTICIPATION],
            sent_to_rep=True,
        ),
    )


def test_the_latest_answer_for_a_car_is_the_one_applied(
    dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    later = RepParticipation(amount=D(500), effective_date=date(2026, 2, 1))
    # The later answer is inserted first, so only the answer time can put it last.
    for answer, answered_at in ((later, NOW + timedelta(days=1)), (ANSWER_400, NOW)):
        add_question(
            sessions,
            conversation_id,
            MissingField.NEW_CAR_PARTICIPATION,
            ATTO_3_KEY,
            status="answered",
            answer=answer,
            answered_at=answered_at,
        )

    [column] = dataset_service.set_alternatives(conversation_id, [ATTO_3]).alternatives

    assert (column.participation, column.participation_effective_date) == (500, date(2026, 2, 1))


def test_an_answer_applies_only_in_its_own_conversation_and_only_as_a_participation(
    dataset_service: ConversationService, sessions: sessionmaker[Session], app_config: AppConfig
) -> None:
    other = dataset_service.create()
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    add_question(
        sessions,
        other,
        MissingField.NEW_CAR_PARTICIPATION,
        ATTO_3_KEY,
        status="answered",
        answer=ANSWER_400,
    )
    add_question(sessions, conversation_id, MissingField.OCCUPATION, status="answered")

    [column] = dataset_service.set_alternatives(conversation_id, [ATTO_3]).alternatives

    # Neither answer is this car's in this conversation, so the catalog's amount stays.
    car = app_config.catalog.find(ATTO_3_KEY)
    assert car is not None
    assert column.status == "calculated"
    assert (column.participation, column.participation_source) == (
        car.default_participation,
        "catalog",
    )
    assert column.participation_effective_date is None
    assert column.missing == ()


def test_a_family_track_column_carries_the_participation_and_both_shares_of_the_gap(
    dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, {**EMPLOYEE, "children_under_23": 5})
    add_question(
        sessions,
        conversation_id,
        MissingField.NEW_CAR_PARTICIPATION,
        CARNIVAL_KEY,
        status="answered",
        answer=ANSWER_400,
    )

    [column] = dataset_service.set_alternatives(conversation_id, [CARNIVAL]).alternatives

    assert column.status == "calculated"
    # The gap is 6,000 - 4,500 = 1,500 in group C, and it is split evenly.
    assert (column.car_cost, column.car_budget) == (6000, 4500)
    assert (column.participation, column.employee_gap_share, column.employer_gap_share) == (
        400,
        750,
        750,
    )


@pytest.mark.parametrize("partial", [False, True], ids=["panel", "agent"])
def test_the_employee_can_never_set_the_group(
    partial: bool, dataset_service: ConversationService, sessions: sessionmaker[Session]
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    before = dataset_service.state(conversation_id)
    group_error = FieldError("eligibility_group", copy_he.GROUP_FROM_OCCUPATION)

    if partial:
        update = dataset_service.update_profile_partial(conversation_id, {"eligibility_group": "D"})
        assert (update.saved, update.rejected) == ((), (group_error,))
    else:
        with pytest.raises(ProfileValidationError) as caught:
            dataset_service.update_profile(conversation_id, {"eligibility_group": "D"})
        assert caught.value.errors == (group_error,)

    after = dataset_service.state(conversation_id)
    assert after.profile == before.profile
    assert after.employee_group == before.employee_group
    assert (after.employee_group.group, after.employee_group.source) == ("C", "occupation")


def test_a_rep_answer_writes_the_group_and_the_comparison_follows_it(
    dataset_service: ConversationService, engine_view: EngineView
) -> None:
    conversation_id = dataset_service.create()
    dataset_service.update_profile(conversation_id, EMPLOYEE)
    dataset_service.set_alternatives(conversation_id, [SEAL])
    assert comparison_of(dataset_service.state(conversation_id)).alternatives[0].status == (
        "not_eligible"
    )

    with dataset_service.transaction() as session:
        dataset_service.apply_rep_answer(session, conversation_id, {"eligibility_group": "D"})
        dataset_service.recalculate(session, conversation_id)

    state = dataset_service.state(conversation_id)
    assert (state.employee_group.group, state.employee_group.source) == ("D", "representative")
    [column] = comparison_of(state).alternatives
    assert column.eligibility.status == "eligible"
    assert column.car_budget == 5200
    assert state.comparison == engine_view(state)
