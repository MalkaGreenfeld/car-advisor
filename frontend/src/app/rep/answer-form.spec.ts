import { Injector } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { deferred } from '../employee/testing/deferred';
import { ApiClient, ApiError } from '../shared/api';
import { RepQuestionView } from '../shared/api-models';
import { AnswerForm } from './answer-form';
import {
  AnswerFieldName,
  AnswerFormState,
  AnswerKind,
  createAnswerForm,
  initialValuesOf,
} from './answer-values';
import { repCopy } from './rep.copy';

const copy = repCopy.answer;
const ID = 'q1';

function question(overrides: Partial<RepQuestionView> = {}): RepQuestionView {
  return {
    id: ID,
    question_type: 'participation',
    question_text: 'מה ההשתתפות ברכב Kia EV6?',
    created_at: '2026-10-13T09:00:00Z',
    employee: { display_name: 'דנה לוי', number: 4821 },
    eligibility_group: 'B',
    car: { display_name: 'Kia EV6', car_key: '2026-2026-1-01229-00004' },
    suggested_amount: null,
    choices: null,
    status: 'open',
    answer: null,
    ...overrides,
  };
}

function answeredView(overrides: Partial<RepQuestionView> = {}): RepQuestionView {
  return question({
    status: 'answered',
    answer: { amount: '350', effective_date: '2026-01-01', note: null, text: null, choice: null },
    ...overrides,
  });
}

const GROUP_CHOICES = [
  { value: 'A', label: 'א' },
  { value: 'B', label: 'ב' },
  { value: 'C', label: 'ג' },
  { value: 'D', label: 'ד' },
];

function createFakeApi() {
  return { answerRepQuestion: vi.fn<ApiClient['answerRepQuestion']>() };
}

describe('AnswerForm', () => {
  let api: ReturnType<typeof createFakeApi>;
  let fixture: ComponentFixture<AnswerForm>;
  let state: AnswerFormState;
  let answered: ReturnType<typeof vi.fn<(view: RepQuestionView) => void>>;
  let alreadyAnswered: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    api = createFakeApi();
    TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: api }] });
  });

  async function open(
    kind: AnswerKind = 'participation',
    overrides: Partial<RepQuestionView> = {},
  ): Promise<void> {
    const types = { participation: 'participation', choice: 'eligibility_group', text: 'other' };
    const shown = question({
      question_type: types[kind] as RepQuestionView['question_type'],
      ...(kind === 'choice' ? { car: null, choices: GROUP_CHOICES } : {}),
      ...overrides,
    });
    state = createAnswerForm(kind, TestBed.inject(Injector), initialValuesOf(shown));
    fixture = TestBed.createComponent(AnswerForm);
    fixture.componentRef.setInput('question', shown);
    fixture.componentRef.setInput('state', state);
    answered = vi.fn();
    alreadyAnswered = vi.fn();
    fixture.componentInstance.answered.subscribe(answered);
    fixture.componentInstance.alreadyAnswered.subscribe(alreadyAnswered);
    await settle();
  }

  async function settle(): Promise<void> {
    await fixture.whenStable();
    fixture.detectChanges();
  }

  function host(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function control(name: AnswerFieldName): HTMLInputElement | HTMLTextAreaElement {
    const element = host().querySelector<HTMLInputElement | HTMLTextAreaElement>(
      `#answer-${ID}-${name}`,
    );
    if (element === null) {
      throw new Error(`No control for ${name}`);
    }
    return element;
  }

  function type(name: AnswerFieldName, text: string): void {
    const element = control(name);
    element.value = text;
    element.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // The text next to a field, which its aria-describedby points at while the text is showing.
  function messageOf(name: AnswerFieldName): string | null {
    const message = host().querySelector(`#answer-${ID}-${name}-message`);
    if (message === null) {
      return null;
    }
    expect(control(name).getAttribute('aria-describedby')).toBe(message.id);
    return message.textContent?.trim() ?? null;
  }

  function submitButton(): HTMLButtonElement {
    const button = host().querySelector<HTMLButtonElement>('button[type="submit"]');
    if (button === null) {
      throw new Error('No submit button');
    }
    return button;
  }

  async function submit(): Promise<void> {
    submitButton().click();
    await settle();
  }

  function status(): string {
    return host().querySelector('[role="status"]')?.textContent?.trim() ?? '';
  }

  async function fillParticipation(amount = '350', date = '2026-01-01', note = ''): Promise<void> {
    type('amount', amount);
    type('effective_date', date);
    type('note', note);
    await settle();
  }

  describe('a participation question', () => {
    it('has an amount for numbers, a date field and an optional note, each with a label', async () => {
      await open();

      expect(control('amount').getAttribute('inputmode')).toBe('decimal');
      expect(control('effective_date').getAttribute('type')).toBe('date');
      expect(control('note').getAttribute('type')).toBe('text');
      for (const label of host().querySelectorAll<HTMLLabelElement>('label')) {
        expect(label.control).not.toBeNull();
      }
      expect(host().querySelector('textarea')).toBeNull();
    });

    it.each<[string, AnswerFieldName, string, string, string]>([
      ['a missing amount', 'amount', '', '2026-01-01', copy.errors.amount],
      ['a negative amount', 'amount', '-1', '2026-01-01', copy.errors.amount],
      ['an amount that is not a number', 'amount', 'abc', '2026-01-01', copy.errors.amount],
      ['an amount with three decimals', 'amount', '12.345', '2026-01-01', copy.errors.amount],
      ['a missing date', 'effective_date', '350', '', copy.errors.effective_date],
    ])(
      'rejects %s next to the field and sends nothing',
      async (_kind, field, amount, date, message) => {
        await open();
        await fillParticipation(amount, date);

        await submit();

        expect(api.answerRepQuestion).not.toHaveBeenCalled();
        expect(control(field).getAttribute('aria-invalid')).toBe('true');
        expect(messageOf(field)).toBe(message);
        expect(answered).not.toHaveBeenCalled();
      },
    );

    it('rejects a note that is too long', async () => {
      await open();
      await fillParticipation('350', '2026-01-01', 'x'.repeat(501));

      await submit();

      expect(api.answerRepQuestion).not.toHaveBeenCalled();
      expect(messageOf('note')).toBe(copy.errors.note);
    });

    it('moves the focus to the first invalid field', async () => {
      await open();
      await fillParticipation('', '');

      await submit();

      await vi.waitFor(() => expect(document.activeElement).toBe(control('amount')));
    });

    it('sends a valid answer, and tells the page about the answered question', async () => {
      api.answerRepQuestion.mockResolvedValue(answeredView());
      await open();
      await fillParticipation(' 350 ', '2026-01-01', ' Starts in January ');

      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledExactlyOnceWith(ID, {
        participation: {
          amount: '350',
          effective_date: '2026-01-01',
          note: 'Starts in January',
        },
      });
      expect(answered).toHaveBeenCalledExactlyOnceWith(answeredView());
    });

    it('leaves the note out of the request when it is blank', async () => {
      api.answerRepQuestion.mockResolvedValue(answeredView());
      await open();
      await fillParticipation('0', '2026-03-05', '   ');

      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledExactlyOnceWith(ID, {
        participation: { amount: '0', effective_date: '2026-03-05' },
      });
    });

    it('sends once when it is submitted again before the answer arrives', async () => {
      const pending = deferred<RepQuestionView>();
      api.answerRepQuestion.mockReturnValue(pending.promise);
      await open();
      await fillParticipation();

      await submit();
      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledOnce();
      expect(submitButton().textContent?.trim()).toBe(copy.sending);
      expect(submitButton().disabled).toBe(false);
      pending.resolve(answeredView());
      await settle();
      expect(submitButton().textContent?.trim()).toBe(copy.submit);
    });

    it('keeps what was typed in the state that the page owns', async () => {
      await open();

      await fillParticipation('350', '2026-01-01', 'Note');

      expect(state.values()).toEqual({
        amount: '350',
        effective_date: '2026-01-01',
        note: 'Note',
        choice: '',
        text: '',
      });
    });

    it('starts with the catalog amount, shown next to the field, and an empty date', async () => {
      await open('participation', { suggested_amount: '300' });

      expect(control('amount').value).toBe('300');
      expect(control('effective_date').value).toBe('');
      const hint = host().querySelector(`#answer-${ID}-amount-hint`)?.textContent ?? '';
      expect(hint).toContain(copy.suggested.replace('{amount}', '₪300'));
    });

    it('still needs the effective date when it starts with the catalog amount', async () => {
      await open('participation', { suggested_amount: '300' });

      await submit();

      expect(api.answerRepQuestion).not.toHaveBeenCalled();
      expect(messageOf('effective_date')).toBe(copy.errors.effective_date);
      expect(messageOf('amount')).toBeNull();
    });

    it('has no catalog line for a car without a catalog amount', async () => {
      await open();

      expect(control('amount').value).toBe('');
      expect(host().querySelector('.suggested')).toBeNull();
    });
  });

  describe('a choice question', () => {
    it('offers the choices of the question in a required select', async () => {
      await open('choice');

      const select = control('choice') as unknown as HTMLSelectElement;
      expect(select.tagName).toBe('SELECT');
      expect(Array.from(select.options, (option) => option.textContent?.trim())).toEqual([
        copy.choice.none,
        'א',
        'ב',
        'ג',
        'ד',
      ]);
      expect(host().querySelector('label')?.textContent?.trim()).toBe(
        copy.choice.eligibility_group,
      );
    });

    it('rejects no choice and sends nothing', async () => {
      await open('choice');

      await submit();

      expect(api.answerRepQuestion).not.toHaveBeenCalled();
      expect(messageOf('choice')).toBe(copy.errors.choice);
    });

    it('sends the chosen value', async () => {
      api.answerRepQuestion.mockResolvedValue(answeredView({ question_type: 'eligibility_group' }));
      await open('choice');
      const select = control('choice') as unknown as HTMLSelectElement;
      // A browser fires both when the choice changes, and [formField] reads the input event.
      select.value = 'D';
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await settle();

      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledExactlyOnceWith(ID, { choice: 'D' });
    });
  });

  describe('any other question', () => {
    it('has one required free-text field', async () => {
      await open('text');

      expect(control('text').tagName).toBe('TEXTAREA');
      expect(host().querySelector('input')).toBeNull();
    });

    it.each([
      ['empty text', ''],
      ['blank text', ' \t\n '],
      ['text of 2,001 characters', 'x'.repeat(2001)],
    ])('rejects %s and sends nothing', async (_kind, text) => {
      await open('text');
      type('text', text);

      await submit();

      expect(api.answerRepQuestion).not.toHaveBeenCalled();
      expect(messageOf('text')).toBe(copy.errors.text);
      expect(control('text').getAttribute('aria-invalid')).toBe('true');
    });

    it('sends the trimmed text', async () => {
      api.answerRepQuestion.mockResolvedValue(answeredView({ question_type: 'other' }));
      await open('text');
      type('text', '  The occupation allows the waiver.  ');

      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledExactlyOnceWith(ID, {
        text: 'The occupation allows the waiver.',
      });
      expect(answered).toHaveBeenCalledOnce();
    });
  });

  describe('when the answer cannot be saved', () => {
    it('keeps the values and says that the answer was not saved, and a second try can work', async () => {
      api.answerRepQuestion.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      api.answerRepQuestion.mockResolvedValueOnce(answeredView());
      await open();
      await fillParticipation('350', '2026-01-01', 'Note');

      await submit();

      expect(status()).toBe(copy.notSaved);
      expect(answered).not.toHaveBeenCalled();
      expect(state.values().amount).toBe('350');
      expect(control('amount')).toHaveProperty('value', '350');

      await submit();

      expect(api.answerRepQuestion).toHaveBeenCalledTimes(2);
      expect(status()).toBe('');
      expect(answered).toHaveBeenCalledOnce();
    });

    it('shows the message of the server next to each field that it names, until the text changes', async () => {
      api.answerRepQuestion.mockRejectedValueOnce(
        new ApiError(422, 'invalid_answer', [
          { field: 'amount', message: 'הסכום אינו תקין לפי השרת.' },
          { field: 'effective_date', message: 'התאריך אינו תקין לפי השרת.' },
        ]),
      );
      await open();
      await fillParticipation();

      await submit();

      expect(messageOf('amount')).toBe('הסכום אינו תקין לפי השרת.');
      expect(messageOf('effective_date')).toBe('התאריך אינו תקין לפי השרת.');
      expect(control('amount').getAttribute('aria-invalid')).toBe('true');
      expect(state.values().amount).toBe('350');

      type('amount', '360');
      await settle();

      expect(messageOf('amount')).toBeNull();
      expect(control('amount').hasAttribute('aria-invalid')).toBe(false);
      expect(messageOf('effective_date')).toBe('התאריך אינו תקין לפי השרת.');
    });

    it('says that the question was answered already, and asks the page for the stored answer', async () => {
      api.answerRepQuestion.mockRejectedValueOnce(new ApiError(409, 'already_answered'));
      await open();
      await fillParticipation();

      await submit();

      expect(status()).toBe(copy.alreadyAnswered);
      expect(alreadyAnswered).toHaveBeenCalledOnce();
      expect(answered).not.toHaveBeenCalled();
    });
  });
});
