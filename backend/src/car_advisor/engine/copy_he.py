from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from car_advisor.engine.types import CarKey, Group, MissingField

# All Hebrew copy of the engine lives in this file, so a wording change touches one place.

GROUP_LETTERS: Mapping[Group, str] = {
    Group.A: "א",
    Group.B: "ב",
    Group.C: "ג",
    Group.D: "ד",
}

MISSING_FIELD_LABELS: Mapping[MissingField, str] = {
    MissingField.ELIGIBILITY_GROUP: "קבוצת זכאות",
    MissingField.BASE_SALARY: "שכר בסיס",
    MissingField.PAY_COMPONENTS: "רכיבי שכר נוספים",
    MissingField.PENSION_RATE: "אחוז הפרשת העובד לפנסיה",
    MissingField.STUDY_FUND_RATE: "אחוז הפרשת העובד לקרן השתלמות",
    MissingField.CREDIT_POINTS: "נקודות זיכוי",
    MissingField.OCCUPATION: "עיסוק",
    MissingField.CHILDREN_UNDER_23: "מספר ילדים עד גיל 23",
    MissingField.CURRENT_CAR: "הרכב הנוכחי",
    MissingField.CURRENT_CAR_PARTICIPATION: "השתתפות ברכב הנוכחי",
    MissingField.NEW_CAR_PARTICIPATION: "השתתפות ברכב החדש",
    MissingField.CAR_GROUP: "קבוצת הרכב",
    MissingField.POWERTRAIN: "סוג הנעה",
}


def car_group_reason(car_group: Group, employee_group: Group, *, allowed: bool) -> str:
    verdict = "מותר" if allowed else "אסור"
    car, employee = GROUP_LETTERS[car_group], GROUP_LETTERS[employee_group]
    return f"רכב מקבוצה {car} {verdict} לעובד בקבוצה {employee}."


def family_track_reason(children: int, threshold: int, *, allowed: bool) -> str:
    verdict = "מותר" if allowed else "אסור"
    return (
        f"רכב במסלול ברוכת ילדים {verdict} לעובד: מספר הילדים עד גיל 23 הוא {children}, "
        f"והסף למסלול הוא {threshold}."
    )


def waiver_reason(occupation: str, *, allowed: bool) -> str:
    verdict = "מאפשר" if allowed else "אינו מאפשר"
    return f'העיסוק "{occupation}" {verdict} ויתור על רכב.'


def missing_data(fields: Sequence[MissingField]) -> str:
    *leading, last = (MISSING_FIELD_LABELS[field] for field in fields)
    names = f"{', '.join(leading)} ו{last}" if leading else last
    return f"חסר מידע לבדיקת הזכאות: {names}."


def occupation_not_provided() -> str:
    return f"{missing_data([MissingField.OCCUPATION])} העיסוק חסר בפרופיל העובד."


def occupation_not_in_table(occupation: str) -> str:
    missing = missing_data([MissingField.OCCUPATION])
    return f'{missing} העיסוק "{occupation}" לא נמצא בטבלת העיסוקים.'


# The Explanation (Req 8.9). {a} stands for an amount in absolute value with a thousands comma,
# and the ₪ sign comes before it (Req 16.3). The preposition takes a maqaf (U+05BE) and not a
# hyphen, because the UI reads a hyphen in front of an amount as a minus sign.
EXPLANATION_HEADLINE_UP = "הנטו עולה ב\u05be₪{a} בחודש"
EXPLANATION_HEADLINE_DOWN = "הנטו יורד ב\u05be₪{a} בחודש"
EXPLANATION_HEADLINE_UNCHANGED = "הנטו לא משתנה"

# "And" is a prefix of the word it joins.
EXPLANATION_AND = "ו"


@dataclass(frozen=True)
class ComponentPhrases:
    raises: str
    lowers: str


USAGE_VALUE_PHRASES = ComponentPhrases(
    raises="שווי השימוש הנמוך חוסך ₪{a}",
    lowers="שווי השימוש הגבוה עולה לך ₪{a}",
)
EV_REFUND_PHRASES = ComponentPhrases(
    raises="החזר החשמל מוסיף ₪{a}",
    lowers="אובדן החזר החשמל מוריד ₪{a}",
)
PARTICIPATION_PHRASES = ComponentPhrases(
    raises="השתתפות נמוכה יותר מוסיפה ₪{a}",
    lowers="השתתפות גבוהה יותר מורידה ₪{a}",
)
CAR_MAINTENANCE_PHRASES = ComponentPhrases(
    raises="אחזקת רכב מוסיפה ₪{a}",
    lowers="אובדן אחזקת רכב מוריד ₪{a}",
)

_AMOUNT_RANGE = "יש להזין סכום בשקלים, 0 ומעלה."
_PERCENT_RANGE = "יש להזין אחוז בין 0 ל-100."

# The allowed values of every Employee_Profile field, keyed by the field name (Req 11.3).
PROFILE_FIELD_RANGES: Mapping[str, str] = {
    "display_name": "יש להזין שם כטקסט.",
    "eligibility_group": "יש לבחור קבוצה: א, ב, ג או ד.",
    "base_salary": _AMOUNT_RANGE,
    "pay_components": "יש להזין לכל רכיב שכר סוג (עמלה או בונוס), תיאור וסכום בשקלים, 0 ומעלה.",
    "pension_rate_percent": _PERCENT_RANGE,
    "study_fund_rate_percent": _PERCENT_RANGE,
    "credit_points": "יש להזין מספר נקודות זיכוי, 0 ומעלה.",
    "occupation": "יש להזין את העיסוק כטקסט.",
    "children_under_23": "יש להזין מספר שלם, 0 ומעלה.",
    "has_current_car": "יש לבחור כן או לא.",
    "current_car": "יש להזין יצרן, דגם ושנת רישום.",
    "current_car_key": "יש לבחור את הרכב מתוצאות החיפוש.",
    "current_car_participation": _AMOUNT_RANGE,
}

# The allowed range of a change to a field that Employee_Profile does not have.
UNKNOWN_PROFILE_FIELD = "שדה לא מוכר."

# What a car search lacks when it is rejected (Req 2.13), keyed by the name of the query parameter.
SEARCH_INPUT_REQUIRED: Mapping[str, str] = {
    "registration_year": "יש להזין שנת רישום בת ארבע ספרות.",
    "text": "יש להזין שם יצרן או דגם.",
}

# What each field of an answer of Transport_Rep requires (Req 12.7), keyed by the name of the field.
REP_ANSWER_FIELD_ERRORS: Mapping[str, str] = {
    "amount": "יש להזין סכום השתתפות בשקלים, 0 ומעלה, עד שתי ספרות אחרי הנקודה.",
    "effective_date": "יש להזין תאריך תחילה תקף.",
    "note": "ההערה ארוכה מדי, עד 500 תווים.",
    "text": "יש להזין תשובה בטקסט, עד 2,000 תווים.",
}

# The Rep_Question texts (Req 10.2). Each one names the employee by label and group, and at most
# one car or one occupation. Salary data has no place in a text, because Transport_Rep must not
# see it (Req 12.4), so no template takes an amount.


def employee_label(number: int) -> str:
    # For an employee without a display name. The number is stored on the conversation, and is
    # not derived from its ID, which Transport_Rep must never receive (Req 12.5).
    return f"עובד/ת {number}"


def rep_question_subject(label: str, group: Group | None) -> str:
    if group is None:
        return label
    return f"{label} מקבוצה {GROUP_LETTERS[group]}"


def new_car_participation_question(subject: str, car_name: str, car_key: CarKey) -> str:
    return (
        f"{subject}: מהי ההשתתפות החודשית בשקלים ברכב {car_name} (מפתח רשמי {car_key}), "
        "ומאיזה תאריך היא בתוקף?"
    )


def occupation_waiver_question(subject: str, occupation: str) -> str:
    return f'{subject}: האם העיסוק "{occupation}", שאינו מופיע בטבלת העיסוקים, מאפשר ויתור על רכב?'


def current_car_question(subject: str) -> str:
    return f"{subject}: לא ניתן לזהות את הרכב הנוכחי. מהם היצרן, הדגם ושנת הרישום של הרכב?"


def current_car_participation_question(subject: str) -> str:
    return f"{subject}: מהי ההשתתפות החודשית בשקלים ברכב הנוכחי?"


# The Agent_Tool texts. This is the action that failed when a tool returns an error (Req 9.17),
# keyed by the tool name, so the bot can say which action it was.
TOOL_ACTION_LABELS: Mapping[str, str] = {
    "lookup_car": "חיפוש הרכב",
    "list_catalog_cars": "הצגת קטלוג הרכבים",
    "check_eligibility": "בדיקת הזכאות",
    "compare_alternatives": "חישוב ההשוואה",
    "ask_rep": "שליחת השאלה לנציג",
    "update_profile": "שמירת הפרטים",
}

# Keyed by the Powertrain values.
POWERTRAIN_LABELS: Mapping[str, str] = {
    "electric": "חשמלי",
    "hybrid": "היברידי",
    "plug_in": "היברידי נטען",
    "conventional": "קונבנציונלי",
}


# What the bot tells the employee once the question reached Transport_Rep, in the wording of the
# challenge: "I'm missing X, I asked the representative" (Req 9.6, 10.3).
def rep_question_sent(label: str) -> str:
    return f"חסר לי {label}, שאלתי את הנציג"


# What it tells instead when the question could not be stored (Req 10.16).
def rep_question_not_sent(label: str) -> str:
    return f"חסר לי {label}, אבל לא הצלחתי לשלוח את השאלה לנציג"


# The texts that the Advisor_Agent instructions hand to the model.

# The reply to a question that is not about the company car or the net pay, word for word
# (Req 9.13). It holds no number, so the number guard lets it through without a tool result.
OFF_TOPIC_REPLY = "אני כאן כדי לעזור רק בשאלות על רכב החברה ועל הנטו."

# Examples of the combined feminine/masculine forms that the employee is addressed in (Req 9.10).
COMBINED_FORM_EXAMPLES = "את/ה, בחר/י, מעוניין/ת"

# The Hebrew term for each amount of a comparison result, keyed by the name the result gives it, so
# that the bot names an amount in the words of the challenge and of the explanation above.
COMPARISON_TERMS: Mapping[str, str] = {
    "net_pay": "נטו",
    "difference": "הפרש",
    "usage_value": "שווי שימוש",
    "ev_refund": "החזר חשמל",
    "participation": "השתתפות",
    "car_maintenance": "אחזקת רכב",
    "car_cost": "עלות הרכב",
    "car_budget": "תקציב הרכב",
    "employer_gap_share": "חלק המעסיק בפער",
    "unused_budget": "תקציב שלא נוצל",
}
