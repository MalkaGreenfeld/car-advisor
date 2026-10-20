import {
  afterNextRender,
  Component,
  computed,
  ElementRef,
  inject,
  Injector,
  linkedSignal,
  viewChild,
} from '@angular/core';
import { applyEach, FieldTree, form, validate } from '@angular/forms/signals';

import { ConversationState, PayComponent } from '../../shared/api-models';
import { BidiText } from '../../shared/bidi-text';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { DetailsField, SelectOption } from './details-field';
import {
  amountOf,
  blankRow,
  hasEdits,
  kindOf,
  labelOf,
  listOf,
  PAY_KINDS,
  parseRow,
  PayRow,
  rowOf,
  sameList,
  sameTexts,
  textsOf,
} from './pay-component-rows';
import { SaveQueue } from './save-queue';
import { shownError } from './shown-error';

const copy = employeeCopy.details.payComponents;

const KIND_OPTIONS: readonly SelectOption[] = [
  { value: '', label: copy.choices.noKind },
  ...PAY_KINDS.map((kind) => ({ value: kind, label: copy.choices[kind] })),
];

type Part = keyof typeof copy.fields;

// What the server answered for a list that was not saved. It belongs to the texts that were sent,
// so it goes away as soon as the employee changes one of them.
interface Outcome {
  readonly texts: string;
  readonly message: string;
}

@Component({
  selector: 'app-pay-components',
  imports: [BidiText, DetailsField],
  templateUrl: './pay-components.html',
  styleUrl: './pay-components.scss',
})
export class PayComponents {
  private readonly store = inject(EmployeeStore);
  private readonly queue = inject(SaveQueue);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  protected readonly copy = copy;
  protected readonly legend = employeeCopy.details.fields.pay_components.label;
  protected readonly notEntered = employeeCopy.details.notEntered;
  protected readonly kindOptions = KIND_OPTIONS;
  protected readonly messageId = 'pay-components-message';

  protected readonly stored = computed(() => this.store.state()?.profile.pay_components ?? null);
  private readonly conversationId = computed(() => this.store.state()?.id);
  private readonly addButton = viewChild.required<ElementRef<HTMLButtonElement>>('addButton');
  private nextKey = 0;

  // A reload of the stored list does not replace rows that hold more than the server does, and
  // another conversation replaces all of them.
  protected readonly rows = linkedSignal<ConversationState | null, PayRow[]>({
    source: this.store.state,
    computation: (state, previous) => {
      const stored = state?.profile.pay_components ?? null;
      if (previous !== undefined && previous.source?.id === state?.id) {
        const rows = previous.value;
        if (hasEdits(rows) || sameList(listOf(rows), stored)) {
          return rows;
        }
      }
      return (stored ?? []).map((component) => rowOf(component, this.nextKey++));
    },
  });

  protected readonly form = form(this.rows, (rows) =>
    applyEach(rows, (row) => {
      validate(row.kind, ({ value }) =>
        kindOf(value()) === undefined ? { kind: 'invalid', message: copy.errors.kind } : null,
      );
      validate(row.label, ({ value }) =>
        labelOf(value()) === undefined ? { kind: 'required', message: copy.errors.label } : null,
      );
      validate(row.amount, ({ value }) =>
        amountOf(value()) === undefined
          ? { kind: 'invalid', message: employeeCopy.details.errors.amount }
          : null,
      );
    }),
  );

  private readonly outcome = linkedSignal<string | undefined, Outcome | null>({
    source: this.conversationId,
    computation: () => null,
  });
  protected readonly message = computed(() => {
    const outcome = this.outcome();
    return outcome !== null && outcome.texts === textsOf(this.rows()) ? outcome.message : null;
  });

  protected readonly problemOf = shownError;

  protected controlName(key: number, part: Part): string {
    return `pay-component-${key}-${part}`;
  }

  protected titleId(key: number): string {
    return `pay-component-${key}-title`;
  }

  protected unsavedId(key: number): string {
    return `pay-component-${key}-unsaved`;
  }

  protected titleOf(index: number): string {
    return copy.rowTitle.replace('{number}', String(index + 1));
  }

  protected add(): void {
    const key = this.nextKey++;
    this.rows.update((rows) => [...rows, blankRow(key)]);
    // DetailsField gives its control the id details-<name>.
    afterNextRender(
      () =>
        this.host.nativeElement
          .querySelector<HTMLElement>(`#details-${this.controlName(key, 'kind')}`)
          ?.focus(),
      { injector: this.injector },
    );
  }

  protected remove(key: number): void {
    this.rows.update((rows) => rows.filter((row) => row.key !== key));
    this.save();
    this.focusAdd();
  }

  // The only way to answer that there are none: an empty list is an answer, a missing one is not.
  protected saveNone(): void {
    this.save([]);
    this.focusAdd();
  }

  protected commit(field: FieldTree<string>): void {
    field().markAsTouched();
    this.save();
  }

  private focusAdd(): void {
    afterNextRender(() => this.addButton().nativeElement.focus(), { injector: this.injector });
  }

  private save(explicit?: readonly PayComponent[]): void {
    const conversationId = this.store.state()?.id;
    this.queue.run(() => this.send(conversationId, explicit));
  }

  private async send(
    conversationId: string | undefined,
    explicit: readonly PayComponent[] | undefined,
  ): Promise<void> {
    if (!this.isOpen(conversationId)) {
      return;
    }
    const sent = this.rows();
    const list = explicit ?? listOf(sent);
    if (explicit === undefined && sameList(list, this.stored())) {
      this.tidy(sent);
      return;
    }

    const result = await this.store.saveField({ pay_components: list });
    if (!this.isOpen(conversationId)) {
      return;
    }
    if (!result.ok) {
      const message = result.fields[0]?.message ?? employeeCopy.details.notSaved;
      this.outcome.set({ texts: textsOf(sent), message });
      return;
    }
    this.outcome.set(null);
    this.tidy(sent);
  }

  // The response of an earlier conversation must not touch the one on screen.
  private isOpen(conversationId: string | undefined): boolean {
    return conversationId !== undefined && this.store.state()?.id === conversationId;
  }

  // A row that was sent shows the text as saved, unless the employee has typed on in the meantime.
  private tidy(sent: readonly PayRow[]): void {
    this.rows.update((rows) =>
      rows.map((row) => {
        const before = sent.find((item) => item.key === row.key);
        const component = before === undefined ? undefined : parseRow(before);
        if (before === undefined || component === undefined) {
          return row;
        }
        return sameTexts(before, row) ? rowOf(component, row.key) : { ...row, saved: component };
      }),
    );
  }
}
