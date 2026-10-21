import { Injector } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient } from '../shared/api';
import { RepQuestionView } from '../shared/api-models';
import { answerKindOf, createAnswerForm, initialValuesOf } from './answer-values';
import { RepQuestion } from './rep-question';
import { repCopy } from './rep.copy';

const copy = repCopy.question;
const stored = repCopy.answer.stored;
const CREATED = new Date(2026, 9, 13, 9, 5);

function question(overrides: Partial<RepQuestionView> = {}): RepQuestionView {
  return {
    id: 'q1',
    question_type: 'participation',
    question_text: 'מה ההשתתפות החודשית ברכב Kia EV6 עבור עובד קבוצה ב?',
    created_at: CREATED.toISOString(),
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

describe('RepQuestion', () => {
  let fixture: ComponentFixture<RepQuestion>;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: {} }] });
  });

  async function open(view: RepQuestionView): Promise<void> {
    fixture = TestBed.createComponent(RepQuestion);
    fixture.componentRef.setInput('question', view);
    if (view.status === 'open') {
      const state = createAnswerForm(
        answerKindOf(view),
        TestBed.inject(Injector),
        initialValuesOf(view),
      );
      fixture.componentRef.setInput('formState', state);
    }
    await fixture.whenStable();
  }

  function host(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  // The label and the value of every fact of the question, in the order they are shown.
  function facts(root: ParentNode = host()): [string, string][] {
    return Array.from(root.querySelectorAll('dl > div'), (row): [string, string] => [
      row.querySelector('dt')?.textContent?.trim() ?? '',
      row.querySelector('dd')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
    ]);
  }

  describe('an open question', () => {
    it('shows the fields of Req 12.3 and the answer form', async () => {
      await open(question());

      expect(host().querySelector('.question-text')?.textContent?.trim()).toBe(
        question().question_text,
      );
      expect(facts()).toEqual([
        [copy.labels.type, copy.types.participation],
        [copy.labels.created, '13/10/2026 09:05'],
        [copy.labels.employee, `דנה לוי, ${copy.labels.group} ב`],
        [copy.labels.car, `Kia EV6, ${copy.labels.officialKey} 2026-2026-1-01229-00004`],
        [copy.labels.status, copy.statuses.open],
      ]);
      expect(host().querySelector('app-answer-form form')).not.toBeNull();
      expect(host().querySelector('.stored-answer')).toBeNull();
    });

    it('keeps the name of the car and its Official_Key in left-to-right isolates', async () => {
      await open(question());

      const car = Array.from(host().querySelectorAll('dl > div')).find(
        (row) => row.querySelector('dt')?.textContent?.trim() === copy.labels.car,
      );
      const isolates = Array.from(car?.querySelectorAll('bdi') ?? [], (bdi) => bdi.textContent);

      expect(isolates).toEqual(['Kia EV6', '2026-2026-1-01229-00004']);
      expect(car?.querySelector('bdi[dir="ltr"]')?.textContent).toBe('2026-2026-1-01229-00004');
    });

    it('names the type of a question that is not about a participation', async () => {
      await open(question({ question_type: 'other', car: null }));

      const [type] = facts();
      expect(type).toEqual([copy.labels.type, copy.types.other]);
      expect(host().querySelector('textarea')).not.toBeNull();
    });

    it('offers the table entries for an occupation question', async () => {
      const choices = [
        { value: 'מנהל/ת מוצר', label: 'מנהל/ת מוצר' },
        { value: 'מפתח/ת תוכנה', label: 'מפתח/ת תוכנה' },
      ];
      await open(question({ question_type: 'occupation', car: null, choices }));

      const [type] = facts();
      expect(type).toEqual([copy.labels.type, copy.types.occupation]);
      const select = host().querySelector('select');
      expect(Array.from(select?.options ?? [], (option) => option.value)).toEqual([
        '',
        'מנהל/ת מוצר',
        'מפתח/ת תוכנה',
      ]);
    });

    it('leaves out the car, and the group when the employee has none', async () => {
      await open(question({ car: null, eligibility_group: null }));

      const labels = facts().map(([label]) => label);
      expect(labels).not.toContain(copy.labels.car);
      expect(facts()[2]).toEqual([copy.labels.employee, 'דנה לוי']);
    });

    it('shows the Official_Key alone for a car without a name', async () => {
      await open(question({ car: { display_name: null, car_key: '2026-2019-1-04321-00009' } }));

      expect(facts()[3]).toEqual([
        copy.labels.car,
        `${copy.labels.officialKey} 2026-2019-1-04321-00009`,
      ]);
    });
  });

  describe('an answered question', () => {
    it('shows the amount, the date and the note of a participation, and no form', async () => {
      await open(
        question({
          status: 'answered',
          answer: {
            amount: '1350.00',
            effective_date: '2026-01-01',
            note: 'Starts in January',
            text: null,
            choice: null,
          },
        }),
      );

      const answer = host().querySelector<HTMLElement>('.stored-answer');
      expect(answer?.querySelector('h3')?.textContent?.trim()).toBe(stored.heading);
      expect(facts(answer ?? host())).toEqual([
        [stored.amount, '₪1,350'],
        [stored.effectiveDate, '01/01/2026'],
        [stored.note, 'Starts in January'],
      ]);
      expect(host().querySelector('form')).toBeNull();
      expect(host().querySelector('input, textarea, button')).toBeNull();
      expect(facts()[4]).toEqual([copy.labels.status, copy.statuses.answered]);
    });

    it('shows the text of any other answer, and no form', async () => {
      await open(
        question({
          question_type: 'other',
          status: 'answered',
          answer: {
            amount: null,
            effective_date: null,
            note: null,
            text: 'The occupation allows it.',
            choice: null,
          },
        }),
      );

      const answer = host().querySelector<HTMLElement>('.stored-answer');
      expect(facts(answer ?? host())).toEqual([[stored.text, 'The occupation allows it.']]);
      expect(host().querySelector('form')).toBeNull();
    });

    it('shows a choice by its label', async () => {
      await open(
        question({
          question_type: 'eligibility_group',
          car: null,
          choices: [
            { value: 'C', label: 'ג' },
            { value: 'D', label: 'ד' },
          ],
          status: 'answered',
          answer: { amount: null, effective_date: null, note: null, text: null, choice: 'D' },
        }),
      );

      const answer = host().querySelector<HTMLElement>('.stored-answer');
      expect(facts(answer ?? host())).toEqual([[stored.choice, 'ד']]);
    });

    it('shows a participation without a note without the note row', async () => {
      await open(
        question({
          status: 'answered',
          answer: {
            amount: '0',
            effective_date: '2026-03-05',
            note: null,
            text: null,
            choice: null,
          },
        }),
      );

      const labels = facts(host().querySelector('.stored-answer') ?? host()).map(
        ([label]) => label,
      );
      expect(labels).toEqual([stored.amount, stored.effectiveDate]);
    });
  });
});
