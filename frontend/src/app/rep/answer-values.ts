import { Injector, signal, WritableSignal } from '@angular/core';
import { FieldTree, form, schema, validate } from '@angular/forms/signals';

import { RepQuestionView } from '../shared/api-models';
import { repCopy } from './rep.copy';

// A participation question takes an amount and a date, an occupation or eligibility group question
// one of its choices, and every other kind a text.
export type AnswerKind = 'participation' | 'choice' | 'text';

export type AnswerFieldName = 'amount' | 'effective_date' | 'note' | 'choice' | 'text';

// What the form holds while Transport_Rep types: the text of every input, never a parsed value.
export interface AnswerValues {
  amount: string;
  effective_date: string;
  note: string;
  choice: string;
  text: string;
}

export interface AnswerFormState {
  readonly kind: AnswerKind;
  readonly values: WritableSignal<AnswerValues>;
  readonly form: FieldTree<AnswerValues>;
}

// The widths of the columns that store an answer (backend: api/schemas.py).
export const NOTE_MAX_LENGTH = 500;
export const TEXT_MAX_LENGTH = 2000;

const errors = repCopy.answer.errors;
// DECIMAL(12,2): up to ten digits, and two after the point.
const AMOUNT = /^\d{1,10}(?:\.\d{1,2})?$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function answerKindOf(question: RepQuestionView): AnswerKind {
  switch (question.question_type) {
    case 'participation':
      return 'participation';
    case 'occupation':
    case 'eligibility_group':
      return 'choice';
    case 'other':
      return 'text';
  }
}

// The values a new form starts with. A participation question about a catalog car starts with the
// catalog's amount, while the effective date stays empty, so that Transport_Rep still confirms the
// answer (Req 12.6).
export function initialValuesOf(question: RepQuestionView): Partial<AnswerValues> {
  return question.suggested_amount === null ? {} : { amount: question.suggested_amount };
}

export function isAmount(text: string): boolean {
  return AMOUNT.test(text.trim());
}

// The date input only holds a real date or nothing, but the text may come from anywhere.
export function isCalendarDate(text: string): boolean {
  const match = DATE.exec(text);
  if (match === null) {
    return false;
  }
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  );
}

export function isAnswerText(text: string): boolean {
  return text.trim() !== '' && text.length <= TEXT_MAX_LENGTH;
}

function answerSchema(kind: AnswerKind) {
  return schema<AnswerValues>((path) => {
    if (kind === 'participation') {
      validate(path.amount, ({ value }) =>
        isAmount(value()) ? null : { kind: 'amount', message: errors.amount },
      );
      validate(path.effective_date, ({ value }) =>
        isCalendarDate(value()) ? null : { kind: 'effective_date', message: errors.effective_date },
      );
      validate(path.note, ({ value }) =>
        value().length <= NOTE_MAX_LENGTH ? null : { kind: 'note', message: errors.note },
      );
    } else if (kind === 'choice') {
      validate(path.choice, ({ value }) =>
        value() !== '' ? null : { kind: 'choice', message: errors.choice },
      );
    } else {
      validate(path.text, ({ value }) =>
        isAnswerText(value()) ? null : { kind: 'text', message: errors.text },
      );
    }
  });
}

// The page creates the form of a question once and keeps it, so a reload of the list never
// resets what was typed. The injector is the page's, because the form is not made in a template.
export function createAnswerForm(
  kind: AnswerKind,
  injector: Injector,
  initial: Partial<AnswerValues> = {},
): AnswerFormState {
  const values = signal<AnswerValues>({
    amount: '',
    effective_date: '',
    note: '',
    choice: '',
    text: '',
    ...initial,
  });
  return { kind, values, form: form(values, answerSchema(kind), { injector }) };
}
