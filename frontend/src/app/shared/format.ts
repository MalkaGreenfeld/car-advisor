const MINUS_SIGN = '\u2212';
const commaGrouping = new Intl.NumberFormat('en-US');
const groupLetters = { A: 'א', B: 'ב', C: 'ג', D: 'ד' } as const;

export function formatShekels(amount: number): string {
  const sign = amount < 0 ? MINUS_SIGN : '';
  return `${sign}₪${commaGrouping.format(Math.abs(amount))}`;
}

export function formatSigned(amount: number): string {
  return amount > 0 ? `+${formatShekels(amount)}` : formatShekels(amount);
}

// Rearranges the text instead of parsing it, because `new Date('yyyy-mm-dd')` is UTC midnight,
// which falls on the previous day in time zones west of UTC.
export function formatDate(iso: string): string {
  const [year, month, day] = iso.split('-');
  return `${day}/${month}/${year}`;
}

const twoDigits = (value: number): string => String(value).padStart(2, '0');

// The date and the time of day in the time zone of the browser, as day/month/year hour:minute.
export function formatDateTime(iso: string): string {
  const moment = new Date(iso);
  const date = `${twoDigits(moment.getDate())}/${twoDigits(moment.getMonth() + 1)}/${moment.getFullYear()}`;
  return `${date} ${twoDigits(moment.getHours())}:${twoDigits(moment.getMinutes())}`;
}
export function groupLetter(group: keyof typeof groupLetters): string {
  return groupLetters[group];
}
