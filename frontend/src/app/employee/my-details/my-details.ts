import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { form, schema, validate } from '@angular/forms/signals';

import { ApiClient } from '../../shared/api';
import { ConversationState, FieldError, ProfileChanges } from '../../shared/api-models';
import { groupLetter } from '../../shared/format';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { CurrentCar } from './current-car';
import { DetailsField, SelectOption } from './details-field';
import {
  carryEdits,
  DetailsFieldName,
  DetailsTexts,
  FIELD_KINDS,
  FIELD_NAMES,
  FieldKind,
  FieldValue,
  formatValue,
  isDetailsField,
  parseValue,
  textOf,
  textsOf,
} from './field-values';
import { PayComponents } from './pay-components';
import { SaveQueue } from './save-queue';

const copy = employeeCopy.details;

interface FieldRow {
  readonly type: 'field';
  readonly name: DetailsFieldName;
  readonly label: string;
  readonly hint?: string;
  readonly options?: readonly SelectOption[];
  readonly inputMode?: 'decimal' | 'numeric';
}

// A profile field that has an editor of its own, or the group, which is only shown. The row keeps
// its place in the script order.
interface EditorRow {
  readonly type: 'group' | 'pay_components' | 'current_car';
}

type Row = FieldRow | EditorRow;

const YES_NO_OPTIONS: readonly SelectOption[] = [
  { value: '', label: copy.choices.noAnswer },
  { value: 'yes', label: copy.choices.yes },
  { value: 'no', label: copy.choices.no },
];

const CONTROLS: Record<FieldKind, Pick<FieldRow, 'options' | 'inputMode'>> = {
  yes_no: { options: YES_NO_OPTIONS },
  amount: { inputMode: 'decimal' },
  percent: { inputMode: 'decimal' },
  points: { inputMode: 'decimal' },
  count: { inputMode: 'numeric' },
  text: {},
};

const FIELD_COPY: Record<DetailsFieldName, { label: string; hint?: string }> = copy.fields;

function fieldRow(name: DetailsFieldName): FieldRow {
  return { type: 'field', name, ...FIELD_COPY[name], ...CONTROLS[FIELD_KINDS[name]] };
}

// The order of the challenge script (Req 9.1): the group, which follows from the occupation, then
// the occupation and the salary. The fields that only some alternatives ask for come after it, and
// the name last.
const ROWS: readonly Row[] = [
  { type: 'group' },
  fieldRow('occupation'),
  fieldRow('base_salary'),
  { type: 'pay_components' },
  fieldRow('pension_rate_percent'),
  fieldRow('study_fund_rate_percent'),
  fieldRow('credit_points'),
  fieldRow('has_current_car'),
  { type: 'current_car' },
  fieldRow('current_car_participation'),
  fieldRow('children_under_23'),
  fieldRow('display_name'),
];

// The constraints of EmployeeProfile on the server, so that a value it would reject is not sent.
const DETAILS_SCHEMA = schema<DetailsTexts>((path) => {
  for (const name of FIELD_NAMES) {
    const kind = FIELD_KINDS[name];
    if (kind === 'text') {
      continue;
    }
    validate(path[name], ({ value }) =>
      parseValue(kind, value()) === undefined
        ? { kind: 'invalid', message: copy.errors[kind] }
        : null,
    );
  }
});

interface Problem {
  readonly message: string;
  readonly invalid: boolean;
}

// What the API answered for a field, next to it. It belongs to the text that was sent, so it goes
// away as soon as the employee changes that text.
interface Outcome extends Problem {
  readonly text: string;
}

type Outcomes = Partial<Record<DetailsFieldName, Outcome>>;

// The kind of a field fixes the type of its value, which a computed key cannot show.
function changeOf(name: DetailsFieldName, value: FieldValue): ProfileChanges {
  return { [name]: value } as ProfileChanges;
}

@Component({
  selector: 'app-my-details',
  imports: [CurrentCar, DetailsField, PayComponents],
  providers: [SaveQueue],
  templateUrl: './my-details.html',
  styleUrl: './my-details.scss',
})
export class MyDetails {
  private readonly store = inject(EmployeeStore);
  private readonly queue = inject(SaveQueue);
  private readonly api = inject(ApiClient);

  protected readonly rows = ROWS;
  protected readonly groupCopy = copy.group;

  // The names of the occupations table, offered in the occupation field (Req 11.19). Only a
  // suggestion: if they cannot be read, the field still takes any text.
  protected readonly occupationNames = signal<readonly string[]>([]);

  // The group in use with its source, read-only (Req 11.2).
  protected readonly groupText = computed(() => {
    const view = this.store.state()?.employee_group ?? null;
    if (view === null || view.group === null) {
      return copy.group.unknown;
    }
    const source =
      view.source === 'representative' ? copy.group.fromRepresentative : copy.group.fromOccupation;
    return `${groupLetter(view.group)} (${source})`;
  });

  private readonly profile = computed(() => this.store.state()?.profile ?? null);

  constructor() {
    this.api.getOccupations().then(
      (response) => this.occupationNames.set(response.names),
      () => this.occupationNames.set([]),
    );
  }

  // The text of every input. A reload of the stored profile does not replace the text of a field
  // that the employee is changing, and another conversation replaces all of it.
  private readonly texts = linkedSignal<ConversationState | null, DetailsTexts>({
    source: this.store.state,
    computation: (state, previous) => {
      const stored = textsOf(state?.profile ?? null);
      if (
        previous === undefined ||
        previous.source === null ||
        state === null ||
        previous.source.id !== state.id
      ) {
        return stored;
      }
      return carryEdits(previous.value, textsOf(previous.source.profile), stored);
    },
  });
  protected readonly form = form(this.texts, DETAILS_SCHEMA);

  // A linkedSignal computes again whenever its source signals change, so it follows a computed ID,
  // which changes only when another conversation is shown, and not the state, which reloads.
  private readonly conversationId = computed(() => this.store.state()?.id);
  private readonly outcomes = linkedSignal<string | undefined, Outcomes>({
    source: this.conversationId,
    computation: () => ({}),
  });

  protected problemOf(name: DetailsFieldName): Problem | null {
    const state = this.form[name]();
    const [error] = state.errors();
    if (error !== undefined && state.touched()) {
      return { message: error.message ?? '', invalid: true };
    }
    const outcome = this.outcomes()[name];
    if (outcome === undefined || outcome.text !== state.value()) {
      return null;
    }
    // The profile may have caught up with the text meanwhile, for example through the chat.
    return outcome.text === textOf(this.profile(), name) ? null : outcome;
  }

  protected save(name: DetailsFieldName): void {
    // An invalid value shows its message from the first time that the employee leaves the field.
    this.form[name]().markAsTouched();
    const conversationId = this.store.state()?.id;
    this.queue.run(() => this.commit(name, conversationId));
  }

  private async commit(name: DetailsFieldName, conversationId: string | undefined): Promise<void> {
    const field = this.form[name]();
    const kind = FIELD_KINDS[name];
    const typed = field.value();
    const value = parseValue(kind, typed);
    // An invalid value is not sent. Its message is already showing.
    if (value === undefined || !this.isOpen(conversationId)) {
      return;
    }
    const stored = textOf(this.profile(), name);
    if (typed === stored || formatValue(kind, value) === stored) {
      this.forget(name);
      field.reset(stored);
      return;
    }

    const result = await this.store.saveField(changeOf(name, value));
    if (!this.isOpen(conversationId)) {
      return;
    }
    if (!result.ok) {
      this.explain(name, typed, result.fields);
      return;
    }
    this.forget(name);
    // Text typed while the save was under way stays, and is saved when the employee leaves again.
    if (field.value() === typed) {
      field.reset(textOf(this.profile(), name));
    }
  }

  // The response of an earlier conversation must not touch the one on screen.
  private isOpen(conversationId: string | undefined): boolean {
    return conversationId !== undefined && this.store.state()?.id === conversationId;
  }

  private explain(name: DetailsFieldName, typed: string, rejected: readonly FieldError[]): void {
    if (rejected.length === 0) {
      this.remember(name, { text: typed, message: copy.notSaved, invalid: false });
      return;
    }
    for (const { field, message } of rejected) {
      // A name that is not a field of the panel is shown on the field that was saved.
      const target = isDetailsField(field) ? field : name;
      const text = target === name ? typed : this.form[target]().value();
      this.remember(target, { text, message, invalid: true });
    }
  }

  private remember(name: DetailsFieldName, outcome: Outcome): void {
    this.outcomes.update((outcomes) => ({ ...outcomes, [name]: outcome }));
  }

  private forget(name: DetailsFieldName): void {
    this.outcomes.update((outcomes) => ({ ...outcomes, [name]: undefined }));
  }
}
