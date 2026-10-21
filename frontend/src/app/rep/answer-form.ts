import {
  afterNextRender,
  Component,
  computed,
  ElementRef,
  inject,
  Injector,
  input,
  output,
  signal,
} from '@angular/core';
import { FormField } from '@angular/forms/signals';

import { ApiClient, ApiError } from '../shared/api';
import { RepAnswerRequest, RepQuestionView } from '../shared/api-models';
import { formatShekels } from '../shared/format';
import { AnswerFieldName, AnswerFormState, AnswerValues } from './answer-values';
import { repCopy } from './rep.copy';

const copy = repCopy.answer;

const FIELD_NAMES: readonly AnswerFieldName[] = [
  'amount',
  'effective_date',
  'note',
  'choice',
  'text',
];

function isAnswerField(name: string): name is AnswerFieldName {
  return (FIELD_NAMES as readonly string[]).includes(name);
}

type Notice = 'not_saved' | 'already_answered';

// What the server said about the fields of one submission. It belongs to the text that was sent,
// so it goes away as soon as Transport_Rep changes that text.
interface Rejection {
  readonly sent: AnswerValues;
  readonly messages: Readonly<Partial<Record<AnswerFieldName, string>>>;
}

@Component({
  selector: 'app-answer-form',
  imports: [FormField],
  templateUrl: './answer-form.html',
  styleUrl: './answer-form.scss',
})
export class AnswerForm {
  private readonly api = inject(ApiClient);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  readonly question = input.required<RepQuestionView>();
  // Owned by the page, which keeps it while the list reloads.
  readonly state = input.required<AnswerFormState>();
  readonly answered = output<RepQuestionView>();
  readonly alreadyAnswered = output<void>();

  protected readonly copy = copy;
  protected readonly submitting = signal(false);
  protected readonly form = computed(() => this.state().form);

  private readonly notice = signal<Notice | null>(null);
  private readonly rejection = signal<Rejection | null>(null);

  // The catalog's amount of the car, shown next to the amount field.
  protected readonly suggested = computed(() => {
    const amount = this.question().suggested_amount;
    return amount === null
      ? null
      : copy.suggested.replace('{amount}', formatShekels(Number(amount)));
  });
  protected readonly choices = computed(() => this.question().choices ?? []);
  protected readonly choiceLabel = computed(() =>
    this.question().question_type === 'eligibility_group'
      ? copy.choice.eligibility_group
      : copy.choice.occupation,
  );

  protected readonly noticeText = computed(() => {
    switch (this.notice()) {
      case 'not_saved':
        return copy.notSaved;
      case 'already_answered':
        return copy.alreadyAnswered;
      case null:
        return '';
    }
  });

  protected id(name: AnswerFieldName): string {
    return `answer-${this.question().id}-${name}`;
  }

  protected messageId(name: AnswerFieldName): string {
    return `${this.id(name)}-message`;
  }

  protected hintId(name: AnswerFieldName): string {
    return `${this.id(name)}-hint`;
  }

  // The first error of a field once Transport_Rep has left it or tried to send, else what the
  // server said about the text that was sent.
  protected message(name: AnswerFieldName): string | null {
    const field = this.form()[name]();
    const [error] = field.errors();
    if (error !== undefined && field.touched()) {
      return error.message ?? '';
    }
    const rejection = this.rejection();
    if (rejection !== null && rejection.sent[name] === this.state().values()[name]) {
      return rejection.messages[name] ?? null;
    }
    return null;
  }

  protected describedBy(name: AnswerFieldName, hint = false): string | null {
    if (this.message(name) !== null) {
      return this.messageId(name);
    }
    return hint ? this.hintId(name) : null;
  }

  protected async submit(event: Event): Promise<void> {
    event.preventDefault();
    // Repeated sends are ignored and the button is not disabled, so it keeps the focus.
    if (this.submitting()) {
      return;
    }
    const root = this.form();
    root().markAsTouched();
    if (root().invalid()) {
      afterNextRender(() => this.focusFirstInvalid(), { injector: this.injector });
      return;
    }
    this.submitting.set(true);
    this.notice.set(null);
    this.rejection.set(null);
    const sent = { ...this.state().values() };
    try {
      this.answered.emit(
        await this.api.answerRepQuestion(this.question().id, this.requestOf(sent)),
      );
    } catch (failure) {
      this.fail(failure, sent);
    } finally {
      this.submitting.set(false);
    }
  }

  private requestOf(values: AnswerValues): RepAnswerRequest {
    const kind = this.state().kind;
    if (kind === 'text') {
      return { text: values.text.trim() };
    }
    if (kind === 'choice') {
      return { choice: values.choice };
    }
    const note = values.note.trim();
    return {
      participation: {
        amount: values.amount.trim(),
        effective_date: values.effective_date,
        ...(note === '' ? {} : { note }),
      },
    };
  }

  private fail(failure: unknown, sent: AnswerValues): void {
    if (!(failure instanceof ApiError)) {
      throw failure;
    }
    if (failure.code === 'already_answered') {
      this.notice.set('already_answered');
      this.alreadyAnswered.emit();
      return;
    }
    const messages: Partial<Record<AnswerFieldName, string>> = {};
    for (const { field, message } of failure.fields) {
      if (isAnswerField(field)) {
        messages[field] = message;
      }
    }
    if (failure.code === 'invalid_answer' && Object.keys(messages).length > 0) {
      this.rejection.set({ sent, messages });
      afterNextRender(() => this.focusFirstInvalid(), { injector: this.injector });
      return;
    }
    this.notice.set('not_saved');
  }

  private focusFirstInvalid(): void {
    this.host.nativeElement.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }
}
