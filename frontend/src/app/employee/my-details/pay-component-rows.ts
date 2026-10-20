import { PayComponent, PayComponentKind } from '../../shared/api-models';
import { parseValue } from './field-values';

// A row of the pay components editor. The three texts are what the employee typed, and saved is
// what the server holds for the row, or null for a row that was never saved.
export interface PayRow {
  readonly key: number;
  readonly kind: string;
  readonly label: string;
  readonly amount: string;
  readonly saved: PayComponent | null;
}

export const PAY_KINDS: readonly PayComponentKind[] = ['commission', 'bonus'];

export function kindOf(text: string): PayComponentKind | undefined {
  return PAY_KINDS.find((kind) => kind === text);
}

export function labelOf(text: string): string | undefined {
  const label = text.trim();
  return label === '' ? undefined : label;
}

// Blank text is no amount here, although it clears an amount field of the panel.
export function amountOf(text: string): string | undefined {
  const amount = parseValue('amount', text);
  return typeof amount === 'string' ? amount : undefined;
}

export function parseRow(row: PayRow): PayComponent | undefined {
  const kind = kindOf(row.kind);
  const label = labelOf(row.label);
  const amount = amountOf(row.amount);
  return kind !== undefined && label !== undefined && amount !== undefined
    ? { kind, label, amount }
    : undefined;
}

export function rowOf(component: PayComponent, key: number): PayRow {
  return { key, ...component, saved: component };
}

export function blankRow(key: number): PayRow {
  return { key, kind: '', label: '', amount: '', saved: null };
}

// What the server should hold for the row. A row that is not valid is not sent, so the list keeps
// it as it was last saved, and leaves out a row that was never saved.
export function entryOf(row: PayRow): PayComponent | null {
  return parseRow(row) ?? row.saved;
}

export function listOf(rows: readonly PayRow[]): PayComponent[] {
  return rows.flatMap((row) => entryOf(row) ?? []);
}

function sameComponent(a: PayComponent, b: PayComponent): boolean {
  return a.kind === b.kind && a.label === b.label && Number(a.amount) === Number(b.amount);
}

// A missing list and an empty one are the same here: the employee has no component to show.
export function sameList(a: readonly PayComponent[], b: readonly PayComponent[] | null): boolean {
  const other = b ?? [];
  return a.length === other.length && a.every((item, index) => sameComponent(item, other[index]));
}

// Whether some row holds more than the server does: an edit, a text that is not valid, or a new
// row.
export function hasEdits(rows: readonly PayRow[]): boolean {
  return rows.some((row) => {
    const parsed = parseRow(row);
    return row.saved === null || parsed === undefined || !sameComponent(parsed, row.saved);
  });
}

export function sameTexts(a: PayRow, b: PayRow): boolean {
  return a.kind === b.kind && a.label === b.label && a.amount === b.amount;
}

export function textsOf(rows: readonly PayRow[]): string {
  return JSON.stringify(rows.map(({ kind, label, amount }) => [kind, label, amount]));
}
