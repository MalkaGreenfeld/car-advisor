import { EMPTY_PROFILE } from '../testing/conversation-state';
import {
  carryEdits,
  FieldKind,
  FieldValue,
  formatValue,
  parseValue,
  textsOf,
} from './field-values';

describe('parseValue', () => {
  it.each<[FieldKind, string, FieldValue | undefined]>([
    ['amount', '14200', '14200'],
    ['amount', ' 14,200.50 ', '14200.50'],
    ['amount', '0', '0'],
    ['amount', '14,20', undefined],
    ['amount', '1,2345', undefined],
    ['amount', '.5', undefined],
    ['amount', '5.', undefined],
    ['amount', '1e3', undefined],
    ['amount', '-1', undefined],
    ['amount', '\u22121', undefined],
    ['amount', '\uff11\uff12', undefined],
    ['points', '2.25', '2.25'],
    ['points', '-0.25', undefined],
    ['percent', '100', '100'],
    ['percent', '100.000', '100.000'],
    ['percent', '99.99', '99.99'],
    ['percent', '100.01', undefined],
    ['percent', '100.00000000000000001', undefined],
    ['percent', '1,000', undefined],
    ['count', '0', 0],
    ['count', '3', 3],
    ['count', '2.5', undefined],
    ['count', '-1', undefined],
    ['count', '9007199254740993', undefined],
    ['group', 'A', 'A'],
    ['group', 'D', 'D'],
    ['group', 'E', undefined],
    ['group', 'a', undefined],
    ['yes_no', 'yes', true],
    ['yes_no', 'no', false],
    ['yes_no', 'maybe', undefined],
    ['text', '  מורה ', 'מורה'],
  ])('reads %s text %j as %j', (kind, text, expected) => {
    expect(parseValue(kind, text)).toBe(expected);
  });

  it.each<FieldKind>(['group', 'yes_no', 'amount', 'percent', 'points', 'count', 'text'])(
    'reads blank %s text as a field that is cleared',
    (kind) => {
      expect(parseValue(kind, '')).toBeNull();
      expect(parseValue(kind, ' \t')).toBeNull();
    },
  );
});

describe('formatValue', () => {
  it.each<[FieldKind, FieldValue, string]>([
    ['yes_no', true, 'yes'],
    ['yes_no', false, 'no'],
    ['yes_no', null, ''],
    ['count', 0, '0'],
    ['amount', '14200.50', '14200.50'],
    ['group', 'C', 'C'],
    ['text', null, ''],
  ])('writes %s value %j as %j', (kind, value, expected) => {
    expect(formatValue(kind, value)).toBe(expected);
  });
});

describe('carryEdits', () => {
  const startedFrom = textsOf({
    ...EMPTY_PROFILE,
    base_salary: '14200',
    pension_rate_percent: '6',
  });
  const stored = textsOf({ ...EMPTY_PROFILE, base_salary: '15000', pension_rate_percent: '7' });

  it('keeps the text of a field that differs from where it started, and updates the rest', () => {
    const typed = { ...startedFrom, base_salary: '15' };

    expect(carryEdits(typed, startedFrom, stored)).toEqual({ ...stored, base_salary: '15' });
  });

  it('shows the stored text everywhere when nothing was edited', () => {
    expect(carryEdits(startedFrom, startedFrom, stored)).toEqual(stored);
  });
});
