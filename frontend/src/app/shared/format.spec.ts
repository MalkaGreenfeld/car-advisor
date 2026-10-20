import { formatDate, formatDateTime, formatShekels, formatSigned, groupLetter } from './format';

describe('formatShekels', () => {
  it('puts ₪ before the number and a comma between thousands', () => {
    expect(formatShekels(12345)).toBe('₪12,345');
  });

  it('groups a seven-digit amount', () => {
    expect(formatShekels(1234567)).toBe('₪1,234,567');
  });

  it('formats zero as ₪0', () => {
    expect(formatShekels(0)).toBe('₪0');
  });

  it('puts U+2212 before ₪ for a negative amount', () => {
    expect(formatShekels(-12345)).toBe('\u2212₪12,345');
  });
});

describe('formatSigned', () => {
  it('gives an increase a plus sign', () => {
    expect(formatSigned(210)).toBe('+₪210');
  });

  it('gives a decrease U+2212', () => {
    expect(formatSigned(-120)).toBe('\u2212₪120');
  });

  it('formats zero as ₪0 without a sign', () => {
    expect(formatSigned(0)).toBe('₪0');
  });

  it('groups a seven-digit amount', () => {
    expect(formatSigned(-1234567)).toBe('\u2212₪1,234,567');
  });
});

describe('formatDate', () => {
  it('turns an ISO date into dd/mm/yyyy', () => {
    expect(formatDate('2026-03-05')).toBe('05/03/2026');
  });
});

describe('groupLetter', () => {
  it('maps each group to its Hebrew letter', () => {
    expect((['A', 'B', 'C', 'D'] as const).map(groupLetter)).toEqual(['א', 'ב', 'ג', 'ד']);
  });
});

describe('formatDateTime', () => {
  it('writes the day, the month, the year and the time of day, in the local time zone', () => {
    const moment = new Date(2026, 9, 13, 9, 5);

    expect(formatDateTime(moment.toISOString())).toBe('13/10/2026 09:05');
  });

  it('pads the day, the month, the hour and the minute to two digits', () => {
    const moment = new Date(2026, 0, 2, 0, 0);

    expect(formatDateTime(moment.toISOString())).toBe('02/01/2026 00:00');
  });
});
