import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient } from '../../shared/api';
import { AlternativeView, ComparisonView, MissingView } from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { alternativeView, conversationState, todayView } from '../testing/conversation-state';
import { ComparisonTable } from './comparison-table';
import { comparisonCopy } from './comparison.copy';

const copy = comparisonCopy;
const MINUS = '\u2212';

// What the engine returns for an alternative that is pending or not eligible.
const NO_RESULT = { net_pay: null, difference: null, breakdown: null, explanation: null } as const;

const WAITING_FOR_PARTICIPATION: MissingView = {
  field: 'new_car_participation',
  label: 'השתתפות ברכב החדש',
  sent_to_rep: true,
};

function pending(position: number, missing: readonly MissingView[]): AlternativeView {
  return alternativeView(position, {
    status: 'pending',
    participation: null,
    participation_effective_date: null,
    participation_source: null,
    missing,
    ...NO_RESULT,
  });
}

function notEligible(position: number): AlternativeView {
  return alternativeView(position, {
    status: 'not_eligible',
    car: { car_key: '2026-2026-1-02001-00007', display_name: 'BMW i4', group: 'D' },
    eligibility: {
      status: 'not_eligible',
      reason: 'רכב מקבוצה ד אסור לעובד בקבוצה ג.',
      is_electric: null,
    },
    car_cost: null,
    car_budget: null,
    participation: null,
    participation_effective_date: null,
    participation_source: null,
    usage_value: null,
    ev_refund: null,
    car_maintenance: null,
    ...NO_RESULT,
  });
}

function underBudget(position: number): AlternativeView {
  return alternativeView(position, { car_cost: 4300, unused_budget: 200 });
}

function familyTrack(position: number): AlternativeView {
  return alternativeView(position, {
    car: { car_key: '2026-2026-1-03001-00002', display_name: 'Kia Carnival', group: 'A' },
    car_cost: 6000,
    car_budget: 3500,
    participation: 400,
    participation_effective_date: null,
    employer_gap_share: 1250,
    employee_gap_share: 1250,
    usage_value: 3200,
    ev_refund: 0,
    net_pay: 12355,
    difference: -1880,
    breakdown: { usage_value: -230, ev_refund: 0, participation: -1650, car_maintenance: 0 },
    explanation: [
      'הנטו יורד ב\u05be₪1,880 בחודש: שווי השימוש הגבוה עולה לך ₪230,',
      'והשתתפות גבוהה יותר מורידה ₪1,650.',
    ],
  });
}

function waiver(position: number): AlternativeView {
  return alternativeView(position, {
    car: null,
    eligibility: {
      status: 'eligible',
      reason: 'העיסוק "נהג" מאפשר ויתור על רכב.',
      is_electric: null,
    },
    car_cost: null,
    car_budget: null,
    participation: 0,
    participation_effective_date: null,
    participation_source: null,
    usage_value: 0,
    ev_refund: 0,
    car_maintenance: 600,
    net_pay: 15305,
    difference: 1070,
    breakdown: { usage_value: 470, ev_refund: 0, participation: 0, car_maintenance: 600 },
    explanation: [
      'הנטו עולה ב\u05be₪1,070 בחודש: שווי השימוש הנמוך חוסך ₪470, ואחזקת רכב מוסיפה ₪600.',
    ],
  });
}

describe('ComparisonTable', () => {
  let store: EmployeeStore;
  let fixture: ComponentFixture<ComparisonTable>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [EmployeeStore, { provide: ApiClient, useValue: {} }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  async function open(comparison: ComparisonView | null): Promise<void> {
    store.show(conversationState('c1', { comparison }));
    fixture = TestBed.createComponent(ComparisonTable);
    await fixture.whenStable();
  }

  function root(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function all(selector: string, scope: ParentNode = root()): Element[] {
    return Array.from(scope.querySelectorAll(selector));
  }

  // Whitespace is collapsed, because the template breaks its lines where the text does not.
  function textOf(element: Element | null | undefined): string {
    return element?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function rowHeaders(): string[] {
    return all('tbody th').map(textOf);
  }

  // The cells of the row with this header, one for each column.
  function rowOf(label: string): HTMLTableCellElement[] {
    const row = all('tbody th').find((th) => textOf(th) === label)?.parentElement;
    if (!row) {
      throw new Error(`No row is headed ${label}`);
    }
    return all('td', row) as HTMLTableCellElement[];
  }

  function valuesOf(label: string): string[] {
    return rowOf(label).map(textOf);
  }

  function linesOf(cell: Element): string[] {
    return all('p', cell).map(textOf);
  }

  describe('the structure', () => {
    beforeEach(() =>
      open({
        today: todayView(),
        alternatives: [underBudget(0), familyTrack(1), waiver(2)],
      }),
    );

    it('has a Today column and a column for each alternative', () => {
      expect(textOf(root().querySelector('caption'))).toBe(copy.caption);
      // The title is the first text of the header. A column with a car also holds its picker.
      expect(all('thead th.column').map((th) => textOf(th.querySelector('app-bidi-text')))).toEqual(
        [copy.today, `${copy.alternative} 1`, `${copy.alternative} 2`, `${copy.alternative} 3`],
      );
      expect(all('thead th').map((th) => th.getAttribute('scope'))).toEqual(Array(5).fill('col'));
    });

    it('has a row for each item of the submission, in the order of Req 8.5', () => {
      expect(rowHeaders()).toEqual([
        copy.rows.car,
        copy.rows.eligibility,
        copy.rows.car_cost,
        copy.rows.car_budget,
        copy.rows.unused_budget,
        copy.rows.participation,
        copy.rows.employee_gap_share,
        copy.rows.employer_gap_share,
        copy.rows.usage_value,
        copy.rows.ev_refund,
        copy.rows.car_maintenance,
        copy.rows.net_pay,
        copy.rows.difference,
        copy.breakdown.usage_value,
        copy.breakdown.ev_refund,
        copy.breakdownParticipationAndShare,
        copy.breakdown.car_maintenance,
        copy.rows.explanation,
      ]);
      expect(all('tbody th').map((th) => th.getAttribute('scope'))).toEqual(Array(18).fill('row'));
      for (const row of all('tbody tr')) {
        expect(all('td', row)).toHaveLength(4);
      }
    });

    it('can be reached and scrolled with the keyboard', () => {
      const region = root().querySelector('[role="region"]');

      expect(region?.getAttribute('tabindex')).toBe('0');
      expect(region?.getAttribute('aria-label')).toBe(copy.scrollLabel);
      expect(region?.querySelector('table')).not.toBeNull();
    });
  });

  describe('the numbers', () => {
    beforeEach(() =>
      open({ today: todayView(), alternatives: [alternativeView(0), familyTrack(1)] }),
    );

    it('shows each amount as the engine returned it, in whole shekels', () => {
      expect(valuesOf(copy.rows.car_cost)).toEqual(['', '₪4,500', '₪6,000']);
      expect(valuesOf(copy.rows.car_budget)).toEqual(['', '₪4,500', '₪3,500']);
      expect(valuesOf(copy.rows.usage_value)).toEqual(['₪2,620', '₪2,310', '₪3,200']);
      expect(valuesOf(copy.rows.ev_refund)).toEqual(['₪0', '₪250', '₪0']);
      expect(valuesOf(copy.rows.car_maintenance)).toEqual(['₪0', '₪0', '₪0']);
      expect(valuesOf(copy.rows.net_pay)).toEqual(['₪14,235', '₪14,445', '₪12,355']);
    });

    it('signs the difference and each part of it, and shows zero without a sign', () => {
      expect(valuesOf(copy.rows.difference)).toEqual(['', '+₪210', `${MINUS}₪1,880`]);
      expect(valuesOf(copy.breakdown.usage_value)).toEqual(['', '+₪310', `${MINUS}₪230`]);
      expect(valuesOf(copy.breakdown.ev_refund)).toEqual(['', '+₪250', '₪0']);
      expect(valuesOf(copy.breakdownParticipationAndShare)).toEqual([
        '',
        `${MINUS}₪350`,
        `${MINUS}₪1,650`,
      ]);
      expect(valuesOf(copy.breakdown.car_maintenance)).toEqual(['', '₪0', '₪0']);
    });

    it('marks an increase and a decrease for their colors, and leaves zero plain', () => {
      const directionsOf = (label: string): (string | null)[] =>
        rowOf(label).map(
          (cell) => cell.querySelector('bdi')?.getAttribute('data-direction') ?? null,
        );

      expect(directionsOf(copy.rows.difference)).toEqual([null, 'increase', 'decrease']);
      expect(directionsOf(copy.breakdown.ev_refund)).toEqual([null, 'increase', null]);
      expect(directionsOf(copy.rows.net_pay)).toEqual([null, null, null]);
    });

    it('keeps each amount, with its sign and ₪, in one left-to-right isolate', () => {
      const amounts = all('td.numeric app-amount').filter((amount) => textOf(amount) !== '');

      expect(amounts.length).toBeGreaterThan(0);
      for (const amount of amounts) {
        const isolate = amount.querySelector('bdi');
        expect(isolate?.getAttribute('dir')).toBe('ltr');
        expect(textOf(isolate)).toBe(textOf(amount));
      }
    });

    it('shows the explanation of a calculated alternative, one paragraph for each line', () => {
      const [today, withEv, familyTrackCar] = rowOf(copy.rows.explanation);

      expect(linesOf(today)).toEqual([]);
      expect(linesOf(withEv)).toEqual(alternativeView(0).explanation);
      expect(linesOf(familyTrackCar)).toEqual(familyTrack(1).explanation);
    });
  });

  describe('the car', () => {
    it('names the car with its group and Official_Key', async () => {
      await open({ today: todayView(), alternatives: [alternativeView(0)] });

      const [today, alternative] = rowOf(copy.rows.car);

      expect(linesOf(today)).toEqual([
        'Kia Niro',
        `${copy.car.group} ג`,
        `${copy.car.officialKey} 2026-2020-1-01229-00001`,
      ]);
      expect(linesOf(alternative)).toEqual([
        'Hyundai Ioniq 5',
        `${copy.car.group} ג`,
        `${copy.car.officialKey} 2026-2026-1-01229-00004`,
      ]);
    });

    it('keeps the whole name in one isolate, so that its digits stay in their place', async () => {
      await open({ today: todayView(), alternatives: [alternativeView(0)] });

      const [, alternative] = rowOf(copy.rows.car);

      const name = alternative.querySelector('p bdi');
      expect(textOf(name)).toBe('Hyundai Ioniq 5');
      expect(name?.getAttribute('dir')).toBe('auto');
    });

    it('says so for no car today and for giving up the car', async () => {
      await open({
        today: todayView({ car: null }),
        alternatives: [alternativeView(0), waiver(1)],
      });

      const [today, , giveUp] = rowOf(copy.rows.car);

      expect(linesOf(today)).toEqual([copy.car.none]);
      expect(linesOf(giveUp)).toEqual([copy.car.waiver]);
    });

    it('falls back to the Official_Key for a car without a name', async () => {
      const key = '2026-2019-1-04321-00009';
      await open({
        today: todayView({ car: { car_key: key, display_name: null, group: null } }),
        alternatives: [alternativeView(0)],
      });

      expect(linesOf(rowOf(copy.rows.car)[0])).toEqual([key]);
    });

    it('does not say that Today has no car while its car is unknown', async () => {
      await open({
        today: todayView({ status: 'pending', car: null, net_pay: null }),
        alternatives: [pending(0, [WAITING_FOR_PARTICIPATION])],
      });

      expect(linesOf(rowOf(copy.rows.car)[0])).toEqual([]);
    });
  });

  describe('an alternative that is pending or not eligible', () => {
    const NOT_SENT: MissingView = {
      field: 'children_under_23',
      label: 'מספר ילדים עד גיל 23',
      sent_to_rep: false,
    };

    beforeEach(() =>
      open({
        today: todayView(),
        alternatives: [
          alternativeView(0),
          pending(1, [WAITING_FOR_PARTICIPATION, NOT_SENT]),
          notEligible(2),
        ],
      }),
    );

    it('shows no net pay, difference, breakdown or explanation in its column', () => {
      const results = [
        copy.rows.net_pay,
        copy.rows.difference,
        copy.breakdown.usage_value,
        copy.breakdown.ev_refund,
        copy.breakdown.participation,
        copy.breakdown.car_maintenance,
        copy.rows.explanation,
      ];

      for (const label of results) {
        const [, calculated, waiting, refused] = rowOf(label);
        expect(textOf(calculated), label).not.toBe('');
        expect(textOf(waiting), label).toBe('');
        expect(textOf(refused), label).toBe('');
      }
    });

    it('shows the status, the reason, and each missing field with the answer to "sent?"', () => {
      const [, , waiting] = rowOf(copy.rows.eligibility);

      expect(textOf(waiting.querySelector('.status'))).toBe(copy.status.pending);
      expect(textOf(waiting)).toContain('רכב מקבוצה ג מותר לעובד בקבוצה ג.');
      expect(all('li', waiting).map(textOf)).toEqual([
        `השתתפות ברכב החדש: ${copy.missing.sentToRep}`,
        `מספר ילדים עד גיל 23: ${copy.missing.notSent}`,
      ]);
    });

    it('shows the items of a pending column that are already known', () => {
      expect(valuesOf(copy.rows.car_cost)[2]).toBe('₪4,500');
      expect(valuesOf(copy.rows.car_budget)[2]).toBe('₪4,500');
      expect(valuesOf(copy.rows.usage_value)[2]).toBe('₪2,310');
      expect(valuesOf(copy.rows.ev_refund)[2]).toBe('₪250');
      expect(valuesOf(copy.rows.participation)[2]).toBe('');
    });

    it('shows the status and the reason of a column that is not eligible, and no list', () => {
      const [, , , refused] = rowOf(copy.rows.eligibility);

      expect(textOf(refused.querySelector('.status'))).toBe(copy.status.not_eligible);
      expect(textOf(refused)).toContain('רכב מקבוצה ד אסור לעובד בקבוצה ג.');
      expect(refused.querySelector('ul')).toBeNull();
      expect(valuesOf(copy.rows.usage_value)[3]).toBe('');
    });
  });

  describe('Today while it is pending', () => {
    const missing: MissingView[] = [
      { field: 'current_car_participation', label: 'השתתפות ברכב הנוכחי', sent_to_rep: false },
    ];

    it('shows its status and its missing fields, and no result in any column', async () => {
      await open({
        today: todayView({ status: 'pending', participation: null, net_pay: null, missing }),
        alternatives: [pending(0, missing)],
      });

      const [today] = rowOf(copy.rows.eligibility);
      expect(textOf(today.querySelector('.status'))).toBe(copy.status.pending);
      expect(all('li', today).map(textOf)).toEqual([
        `השתתפות ברכב הנוכחי: ${copy.missing.notSent}`,
      ]);
      for (const label of [copy.rows.net_pay, copy.rows.difference, copy.rows.explanation]) {
        expect(valuesOf(label), label).toEqual(['', '']);
      }
    });
  });

  describe('the rows that only some alternatives have', () => {
    it('shows Unused_Budget with a note that it is for information only', async () => {
      await open({ today: todayView(), alternatives: [underBudget(0), alternativeView(1)] });

      const [today, under, exact] = rowOf(copy.rows.unused_budget);

      expect(textOf(under.querySelector('app-amount'))).toBe('₪200');
      expect(textOf(under.querySelector('.note'))).toBe(copy.unusedBudgetNote);
      expect(textOf(today)).toBe('');
      expect(textOf(exact)).toBe('');
    });

    it('shows both shares of the gap, next to the participation of the family track', async () => {
      await open({ today: todayView(), alternatives: [familyTrack(0)] });

      const amounts = rowOf(copy.rows.participation).map((cell) =>
        textOf(cell.querySelector('app-amount')),
      );
      expect(amounts).toEqual(['₪0', '₪400']);
      expect(valuesOf(copy.rows.employee_gap_share)).toEqual(['', '₪1,250']);
      expect(valuesOf(copy.rows.employer_gap_share)).toEqual(['', '₪1,250']);
    });

    it('names the participation part of the difference after the share it holds', async () => {
      await open({ today: todayView(), alternatives: [familyTrack(0)] });

      expect(rowHeaders()).toContain(copy.breakdownParticipationAndShare);
      expect(rowHeaders()).not.toContain(copy.breakdown.participation);
    });

    it('leaves the rows out when no alternative has them', async () => {
      await open({ today: todayView(), alternatives: [alternativeView(0), waiver(1)] });

      expect(rowHeaders()).not.toContain(copy.rows.unused_budget);
      expect(rowHeaders()).not.toContain(copy.rows.employee_gap_share);
      expect(rowHeaders()).not.toContain(copy.rows.employer_gap_share);
      expect(rowHeaders()).toContain(copy.breakdown.participation);
    });
  });

  describe('the effective date of a participation', () => {
    it('is shown next to a participation that the representative provided', async () => {
      await open({
        today: todayView(),
        alternatives: [alternativeView(0, { participation_effective_date: '2026-03-05' })],
      });

      const [today, provided] = rowOf(copy.rows.participation);

      expect(textOf(provided.querySelector('app-amount'))).toBe('₪350');
      expect(textOf(provided.querySelector('.note'))).toBe(`${copy.effectiveFrom} 05/03/2026`);
      expect(textOf(today)).toBe('₪0');
    });

    it('is not shown when the participation has no date', async () => {
      await open({
        today: todayView(),
        alternatives: [alternativeView(0, { participation_effective_date: null })],
      });

      expect(rowOf(copy.rows.participation)[1].querySelector('.note')).toBeNull();
    });
  });

  describe('without a comparison', () => {
    it('shows the empty message instead of a table', async () => {
      await open(null);

      expect(root().querySelector('table')).toBeNull();
      expect(textOf(root())).toBe(copy.empty);
    });
  });

  describe('without alternatives', () => {
    it('shows the Today column alone, with a message that no alternative was examined', async () => {
      await open({ today: todayView(), alternatives: [] });

      expect(all('thead th.column').map(textOf)).toEqual([copy.today]);
      expect(valuesOf(copy.rows.net_pay)).toEqual(['₪14,235']);
      expect(textOf(root().querySelector('.empty'))).toBe(copy.noAlternatives);
    });

    it('leaves out the rows that only an alternative can fill', async () => {
      await open({ today: todayView(), alternatives: [] });

      expect(rowHeaders()).toEqual([
        copy.rows.car,
        copy.rows.eligibility,
        copy.rows.participation,
        copy.rows.usage_value,
        copy.rows.ev_refund,
        copy.rows.car_maintenance,
        copy.rows.net_pay,
      ]);
    });

    it('shows what is missing for Today while it is pending (Req 11.16)', async () => {
      const missing: MissingView[] = [
        { field: 'base_salary', label: 'שכר בסיס חודשי', sent_to_rep: false },
      ];
      await open({
        today: todayView({ status: 'pending', participation: null, net_pay: null, missing }),
        alternatives: [],
      });

      const [today] = rowOf(copy.rows.eligibility);
      expect(textOf(today.querySelector('.status'))).toBe(copy.status.pending);
      expect(all('li', today).map(textOf)).toEqual([`שכר בסיס חודשי: ${copy.missing.notSent}`]);
      expect(valuesOf(copy.rows.net_pay)).toEqual(['']);
    });

    it('does not show the message once there is an alternative', async () => {
      await open({ today: todayView(), alternatives: [alternativeView(0)] });

      expect(root().querySelector('.empty')).toBeNull();
    });
  });

  describe('the source of the participation (Req 6.25-6.26)', () => {
    function fromCatalog(position: number, awaiting: boolean): AlternativeView {
      return alternativeView(position, {
        participation: 300,
        participation_effective_date: null,
        participation_source: 'catalog',
        participation_awaiting_rep: awaiting,
      });
    }

    it('names the source next to the amount, and marks a confirmation that is awaited', async () => {
      await open({
        today: todayView(),
        alternatives: [fromCatalog(0, false), fromCatalog(1, true), alternativeView(2)],
      });

      const [today, plain, awaited, answered] = rowOf(copy.rows.participation);
      expect(today.querySelector('.source')).toBeNull();
      expect(textOf(plain.querySelector('.source'))).toBe(copy.participationSource.catalog);
      expect(plain.querySelector('.tag')).toBeNull();
      expect(textOf(awaited.querySelector('.tag'))).toBe(copy.awaitingRep);
      expect(textOf(answered.querySelector('.source'))).toBe(
        copy.participationSource.representative,
      );
    });

    it('adds one line under the table while a column shows the catalog amount', async () => {
      await open({
        today: todayView(),
        alternatives: [fromCatalog(0, false), fromCatalog(1, true)],
      });

      expect(all('.catalog-note').map(textOf)).toEqual([copy.catalogParticipationNote]);
    });

    it('has no line when no column shows the catalog amount', async () => {
      await open({ today: todayView(), alternatives: [alternativeView(0)] });

      expect(root().querySelector('.catalog-note')).toBeNull();
    });
  });
});
