import { EmployeeProfile, Group } from '../../shared/api-models';

export type FieldKind = 'group' | 'yes_no' | 'amount' | 'percent' | 'points' | 'count' | 'text';

// The profile fields that the panel edits as one input each. The kind fixes what the input holds
// and what the server accepts for it (backend/src/car_advisor/engine/types.py).
export const FIELD_KINDS = {
  eligibility_group: 'group',
  base_salary: 'amount',
  pension_rate_percent: 'percent',
  study_fund_rate_percent: 'percent',
  credit_points: 'points',
  has_current_car: 'yes_no',
  current_car_participation: 'amount',
  occupation: 'text',
  children_under_23: 'count',
  display_name: 'text',
} as const satisfies Partial<Record<keyof EmployeeProfile, FieldKind>>;

export type DetailsFieldName = keyof typeof FIELD_KINDS;

// What every input holds while the employee types: the text, never a parsed value.
export type DetailsTexts = Record<DetailsFieldName, string>;

// A profile value of one of the fields above, as the API carries it.
export type FieldValue = Group | boolean | number | string | null;

export const GROUPS: readonly Group[] = ['A', 'B', 'C', 'D'];

export const FIELD_NAMES = Object.keys(FIELD_KINDS) as DetailsFieldName[];

const PLAIN_DECIMAL = /^\d+(\.\d+)?$/;
const GROUPED_DECIMAL = /^\d{1,3}(,\d{3})+(\.\d+)?$/;
const WHOLE_NUMBER = /^\d+$/;

export function isDetailsField(name: string): name is DetailsFieldName {
  return Object.hasOwn(FIELD_KINDS, name);
}

// Returns undefined for text that the server would reject. Blank text clears the field, as null.
export function parseValue(kind: FieldKind, text: string): FieldValue | undefined {
  const trimmed = text.trim();
  if (trimmed === '') {
    return null;
  }
  switch (kind) {
    case 'group':
      return GROUPS.find((group) => group === trimmed);
    case 'yes_no':
      return trimmed === 'yes' || trimmed === 'no' ? trimmed === 'yes' : undefined;
    case 'amount':
    case 'points':
      return parseDecimal(trimmed);
    case 'percent': {
      const percent = parseDecimal(trimmed);
      return percent !== undefined && atMostHundred(percent) ? percent : undefined;
    }
    case 'count':
      return WHOLE_NUMBER.test(trimmed) && Number.isSafeInteger(Number(trimmed))
        ? Number(trimmed)
        : undefined;
    case 'text':
      return trimmed;
  }
}

export function formatValue(kind: FieldKind, value: FieldValue): string {
  if (value === null) {
    return '';
  }
  return kind === 'yes_no' ? (value ? 'yes' : 'no') : String(value);
}

export function textOf(profile: EmployeeProfile | null, name: DetailsFieldName): string {
  return formatValue(FIELD_KINDS[name], profile?.[name] ?? null);
}

export function textsOf(profile: EmployeeProfile | null): DetailsTexts {
  return Object.fromEntries(
    FIELD_NAMES.map((name) => [name, textOf(profile, name)]),
  ) as DetailsTexts;
}

// A field whose text differs from the stored text it started from is being edited, and keeps its
// text. Every other field shows the new stored text.
export function carryEdits(
  typed: DetailsTexts,
  startedFrom: DetailsTexts,
  stored: DetailsTexts,
): DetailsTexts {
  const texts = { ...stored };
  for (const name of FIELD_NAMES) {
    if (typed[name] !== startedFrom[name]) {
      texts[name] = typed[name];
    }
  }
  return texts;
}

// Digits with an optional fraction, or with thousands commas, which is how amounts are written.
// The result has the plain form that the server reads as a Decimal.
function parseDecimal(text: string): string | undefined {
  if (PLAIN_DECIMAL.test(text)) {
    return text;
  }
  return GROUPED_DECIMAL.test(text) ? text.replaceAll(',', '') : undefined;
}

// Compares the digits, so that a value just above 100 is not rounded down to 100.
function atMostHundred(decimal: string): boolean {
  const [whole, fraction = ''] = decimal.split('.');
  const wholeNumber = Number(whole);
  return wholeNumber < 100 || (wholeNumber === 100 && /^0*$/.test(fraction));
}
