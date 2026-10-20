import { ComponentFixture, TestBed } from '@angular/core/testing';

import { routes } from '../app.routes';
import { Deferred, deferred } from '../employee/testing/deferred';
import { ApiClient, ApiError } from '../shared/api';
import { RepQuestionsResponse, RepQuestionView } from '../shared/api-models';
import { POLL_INTERVAL_MS } from '../shared/poller';
import { sharedCopy } from '../shared/shared.copy';
import { RepPage } from './rep-page';
import { repCopy } from './rep.copy';

const OPEN = repCopy.groups.open;
const ANSWERED = repCopy.groups.answered;

function question(id: string, overrides: Partial<RepQuestionView> = {}): RepQuestionView {
  return {
    id,
    question_type: 'participation',
    question_text: `שאלה ${id}`,
    created_at: '2026-10-13T09:00:00Z',
    employee: { display_name: 'דנה לוי', number: 4821 },
    eligibility_group: 'B',
    car: { display_name: 'Kia EV6', car_key: '2026-2026-1-01229-00004' },
    status: 'open',
    answer: null,
    ...overrides,
  };
}

function answered(id: string, overrides: Partial<RepQuestionView> = {}): RepQuestionView {
  return question(id, {
    status: 'answered',
    answer: { amount: '350', effective_date: '2026-01-01', note: null, text: null },
    ...overrides,
  });
}

function createFakeApi() {
  const api = {
    listRepQuestions: vi.fn<ApiClient['listRepQuestions']>(),
    answerRepQuestion: vi.fn<ApiClient['answerRepQuestion']>(),
  };
  api.listRepQuestions.mockResolvedValue({ questions: [] });
  return api;
}

describe('RepPage', () => {
  let api: ReturnType<typeof createFakeApi>;
  let fixture: ComponentFixture<RepPage>;

  beforeEach(() => {
    vi.useFakeTimers();
    api = createFakeApi();
    TestBed.configureTestingModule({ providers: [{ provide: ApiClient, useValue: api }] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function open(): Promise<void> {
    fixture = TestBed.createComponent(RepPage);
    await settle();
  }

  // Lets the pending requests finish, then renders what they left.
  async function settle(): Promise<void> {
    await vi.advanceTimersByTimeAsync(0);
    fixture.detectChanges();
  }

  async function elapse(ms: number): Promise<void> {
    await vi.advanceTimersByTimeAsync(ms);
    fixture.detectChanges();
  }

  function page(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function texts(selector: string): string[] {
    return Array.from(page().querySelectorAll(selector), (el) => el.textContent?.trim() ?? '');
  }

  function group(heading: string): HTMLElement {
    const match = Array.from(page().querySelectorAll('section')).find(
      (section) => section.querySelector('h2')?.textContent?.trim() === heading,
    );
    if (match === undefined) {
      throw new Error(`No group is headed "${heading}".`);
    }
    return match;
  }

  function itemsIn(heading: string): HTMLElement[] {
    return Array.from(group(heading).querySelectorAll<HTMLElement>('li'));
  }

  // The text of each question of a group. The rest of an item is the facts and the form.
  function textsIn(heading: string): string[] {
    return itemsIn(heading).map(
      (item) => item.querySelector('.question-text')?.textContent?.trim() ?? '',
    );
  }

  function click(label: string): void {
    const matches = Array.from(page().querySelectorAll('button')).filter(
      (button) => button.textContent?.trim() === label,
    );
    expect(matches).toHaveLength(1);
    matches[0].click();
  }

  describe('once the list has loaded', () => {
    it('has one main landmark, one h1 and a labelled h2 for each group', async () => {
      await open();

      expect(page().querySelectorAll('main')).toHaveLength(1);
      expect(texts('h1')).toEqual([repCopy.page.title]);
      expect(texts('h2')).toEqual([OPEN, ANSWERED]);
      for (const section of page().querySelectorAll('section')) {
        const heading = page().querySelector(`#${section.getAttribute('aria-labelledby')}`);
        expect(heading?.tagName).toBe('H2');
      }
    });

    it('lists the open questions before the answered ones, each group oldest first', async () => {
      api.listRepQuestions.mockResolvedValue({
        questions: [
          answered('a2', { created_at: '2026-10-13T08:00:00Z' }),
          question('o2', { created_at: '2026-10-13T09:30:00Z' }),
          answered('a1', { created_at: '2026-10-12T08:00:00Z' }),
          question('o1', { created_at: '2026-10-13T09:00:00Z' }),
        ],
      });
      await open();

      expect(texts('h2')).toEqual([OPEN, ANSWERED]);
      expect(textsIn(OPEN)).toEqual(['שאלה o1', 'שאלה o2']);
      expect(textsIn(ANSWERED)).toEqual(['שאלה a1', 'שאלה a2']);
      expect(group(OPEN).textContent).not.toContain(repCopy.empty.open);
    });

    it('orders by time, not by text, when the server leaves out a zero fraction', async () => {
      api.listRepQuestions.mockResolvedValue({
        questions: [
          question('late', { created_at: '2026-10-13T09:00:00.500000Z' }),
          question('early', { created_at: '2026-10-13T09:00:00Z' }),
        ],
      });
      await open();

      expect(textsIn(OPEN)).toEqual(['שאלה early', 'שאלה late']);
    });

    it('replaces the open list with a message while nothing is open', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [answered('a1')] });
      await open();

      expect(group(OPEN).querySelector('li')).toBeNull();
      expect(group(OPEN).textContent).toContain(repCopy.empty.open);
      expect(textsIn(ANSWERED)).toEqual(['שאלה a1']);
      expect(group(ANSWERED).textContent).not.toContain(repCopy.empty.answered);
    });

    it('says so in both groups when there are no questions', async () => {
      await open();

      expect(group(OPEN).textContent).toContain(repCopy.empty.open);
      expect(group(ANSWERED).textContent).toContain(repCopy.empty.answered);
    });
  });

  describe('before the first list', () => {
    it('shows the loading message until the list arrives', async () => {
      const pending = deferred<RepQuestionsResponse>();
      api.listRepQuestions.mockReturnValue(pending.promise);
      await open();

      expect(page().textContent).toContain(sharedCopy.loading);
      expect(page().querySelector('h2')).toBeNull();

      pending.resolve({ questions: [question('o1')] });
      await settle();

      expect(page().textContent).not.toContain(sharedCopy.loading);
      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });

    it('shows the failed message with a retry button that loads the list', async () => {
      api.listRepQuestions.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open();

      expect(page().textContent).toContain(sharedCopy.failed);
      expect(page().querySelector('h2')).toBeNull();

      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      click(repCopy.page.retry);
      await settle();

      expect(page().textContent).not.toContain(sharedCopy.failed);
      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });

    it('keeps trying on its own after a failed first load', async () => {
      api.listRepQuestions.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open();
      expect(page().textContent).toContain(sharedCopy.failed);

      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      await elapse(POLL_INTERVAL_MS);

      expect(page().textContent).not.toContain(sharedCopy.failed);
      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });
  });

  describe('reloading', () => {
    it('asks for the list again every 3 seconds', async () => {
      await open();
      expect(api.listRepQuestions).toHaveBeenCalledTimes(1);

      await elapse(POLL_INTERVAL_MS);
      expect(api.listRepQuestions).toHaveBeenCalledTimes(2);

      await elapse(POLL_INTERVAL_MS);
      expect(api.listRepQuestions).toHaveBeenCalledTimes(3);
    });

    it('shows what a reload brings', async () => {
      await open();
      expect(textsIn(OPEN)).toEqual([]);

      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      await elapse(POLL_INTERVAL_MS);

      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });

    it('keeps the DOM nodes of the questions that a reload returns again', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1'), question('o2')] });
      await open();
      const before = itemsIn(OPEN);

      api.listRepQuestions.mockResolvedValue({
        questions: [question('o1'), question('o2'), question('o3')],
      });
      await elapse(POLL_INTERVAL_MS);

      const after = itemsIn(OPEN);
      expect(after).toHaveLength(3);
      expect(after[0]).toBe(before[0]);
      expect(after[1]).toBe(before[1]);
    });

    it('moves a question to the answered group once it is answered', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('q1')] });
      await open();

      api.listRepQuestions.mockResolvedValue({ questions: [answered('q1')] });
      await elapse(POLL_INTERVAL_MS);

      expect(textsIn(OPEN)).toEqual([]);
      expect(textsIn(ANSWERED)).toEqual(['שאלה q1']);
    });

    it('reloads at once on Refresh', async () => {
      await open();
      const before = api.listRepQuestions.mock.calls.length;
      api.listRepQuestions.mockResolvedValue({ questions: [question('o9')] });

      click(repCopy.page.refresh);
      await settle();

      expect(api.listRepQuestions).toHaveBeenCalledTimes(before + 1);
      expect(textsIn(OPEN)).toEqual(['שאלה o9']);
    });

    it('stops reloading when the page is destroyed', async () => {
      await open();
      fixture.destroy();
      const before = api.listRepQuestions.mock.calls.length;

      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);

      expect(api.listRepQuestions).toHaveBeenCalledTimes(before);
    });
  });

  describe('a reload that fails', () => {
    beforeEach(() => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1'), answered('a1')] });
    });

    it('keeps the last list and says so until the next reload works', async () => {
      await open();
      api.listRepQuestions.mockRejectedValueOnce(new ApiError(503, 'database_unavailable'));

      await elapse(POLL_INTERVAL_MS);

      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
      expect(textsIn(ANSWERED)).toEqual(['שאלה a1']);
      expect(page().textContent).not.toContain(sharedCopy.failed);
      const live = page().querySelector('[aria-live="polite"]');
      expect(live?.textContent).toContain(repCopy.updateFailed);

      await elapse(POLL_INTERVAL_MS);

      expect(page().textContent).not.toContain(repCopy.updateFailed);
      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });

    it('keeps the message through several failures in a row', async () => {
      await open();
      api.listRepQuestions.mockRejectedValue(new ApiError(0, 'network_error'));

      await elapse(POLL_INTERVAL_MS * 3);

      expect(page().textContent).toContain(repCopy.updateFailed);
      expect(textsIn(OPEN)).toEqual(['שאלה o1']);
    });
  });

  describe('replies that arrive out of order', () => {
    let slow: Deferred<RepQuestionsResponse>;

    beforeEach(() => {
      slow = deferred<RepQuestionsResponse>();
    });

    it('never show an older reply after a newer one', async () => {
      await open();
      api.listRepQuestions.mockReturnValueOnce(slow.promise);
      await elapse(POLL_INTERVAL_MS);
      api.listRepQuestions.mockResolvedValue({ questions: [question('new')] });
      await elapse(POLL_INTERVAL_MS);
      expect(textsIn(OPEN)).toEqual(['שאלה new']);

      slow.resolve({ questions: [question('old')] });
      await settle();

      expect(textsIn(OPEN)).toEqual(['שאלה new']);
    });

    it('show a reply that comes after the next request began, so a slow server is not starved', async () => {
      await open();
      api.listRepQuestions.mockReturnValueOnce(slow.promise);
      await elapse(POLL_INTERVAL_MS);
      api.listRepQuestions.mockReturnValueOnce(new Promise<RepQuestionsResponse>(() => undefined));
      await elapse(POLL_INTERVAL_MS);

      slow.resolve({ questions: [question('late')] });
      await settle();

      expect(textsIn(OPEN)).toEqual(['שאלה late']);
    });
  });

  describe('answering a question', () => {
    function type(id: string, field: string, text: string): void {
      const element = page().querySelector<HTMLInputElement>(`#answer-${id}-${field}`);
      if (element === null) {
        throw new Error(`No ${field} field for ${id}`);
      }
      element.value = text;
      element.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function field(id: string, name: string): HTMLInputElement | null {
      return page().querySelector<HTMLInputElement>(`#answer-${id}-${name}`);
    }

    async function fillIn(id: string): Promise<void> {
      type(id, 'amount', '350');
      type(id, 'effective_date', '2026-01-01');
      await settle();
    }

    const live = () => page().querySelector('[aria-live="polite"]');

    it('has an answer form for every open question and none for an answered one', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1'), answered('a1')] });
      await open();

      expect(itemsIn(OPEN)[0].querySelector('form')).not.toBeNull();
      expect(itemsIn(ANSWERED)[0].querySelector('form')).toBeNull();
      expect(itemsIn(ANSWERED)[0].querySelector('.stored-answer')).not.toBeNull();
    });

    it('keeps what was typed, and the focus, when the list reloads', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      await open();
      await fillIn('o1');
      const amount = field('o1', 'amount');
      amount?.focus();

      api.listRepQuestions.mockResolvedValue({ questions: [question('o1'), question('o2')] });
      await elapse(POLL_INTERVAL_MS);

      expect(field('o1', 'amount')).toBe(amount);
      expect(field('o1', 'amount')?.value).toBe('350');
      expect(field('o1', 'effective_date')?.value).toBe('2026-01-01');
      expect(document.activeElement).toBe(amount);
      expect(field('o2', 'amount')?.value).toBe('');
    });

    it('keeps what was typed when a reload leaves the question out for a moment', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      await open();
      await fillIn('o1');

      api.listRepQuestions.mockResolvedValue({ questions: [] });
      await elapse(POLL_INTERVAL_MS);
      expect(field('o1', 'amount')).toBeNull();

      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      await elapse(POLL_INTERVAL_MS);

      expect(field('o1', 'amount')?.value).toBe('350');
    });

    it('moves the question to the answered group and confirms in the live region', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      api.answerRepQuestion.mockResolvedValue(answered('o1'));
      await open();
      await fillIn('o1');

      click(repCopy.answer.submit);
      await settle();

      expect(api.answerRepQuestion).toHaveBeenCalledExactlyOnceWith('o1', {
        participation: { amount: '350', effective_date: '2026-01-01' },
      });
      expect(group(OPEN).textContent).toContain(repCopy.empty.open);
      expect(itemsIn(ANSWERED)).toHaveLength(1);
      expect(itemsIn(ANSWERED)[0].querySelector('form')).toBeNull();
      expect(itemsIn(ANSWERED)[0].querySelector('.stored-answer')?.textContent).toContain('₪350');
      expect(live()?.textContent).toContain(repCopy.answer.saved);
    });

    it('takes the confirmation away after a while', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      api.answerRepQuestion.mockResolvedValue(answered('o1'));
      await open();
      await fillIn('o1');
      click(repCopy.answer.submit);
      await settle();
      expect(live()?.textContent).toContain(repCopy.answer.saved);

      await elapse(8000);

      expect(live()?.textContent).not.toContain(repCopy.answer.saved);
    });

    it('does not move the other questions when one is answered', async () => {
      api.listRepQuestions.mockResolvedValue({
        questions: [question('o1'), question('o2', { created_at: '2026-10-13T10:00:00Z' })],
      });
      api.answerRepQuestion.mockResolvedValue(answered('o1'));
      await open();
      const second = itemsIn(OPEN)[1];
      await fillIn('o1');

      itemsIn(OPEN)[0].querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
      await settle();

      expect(itemsIn(OPEN)).toEqual([second]);
    });

    it('reloads, and shows the stored answer, when the question was answered already', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      api.answerRepQuestion.mockRejectedValue(new ApiError(409, 'already_answered'));
      await open();
      await fillIn('o1');

      api.listRepQuestions.mockResolvedValue({ questions: [answered('o1')] });
      click(repCopy.answer.submit);
      await settle();

      expect(textsIn(OPEN)).toEqual([]);
      expect(itemsIn(ANSWERED)).toHaveLength(1);
      expect(itemsIn(ANSWERED)[0].querySelector('form')).toBeNull();
      expect(live()?.textContent).not.toContain(repCopy.answer.saved);
    });

    it('keeps the values and says that the answer was not saved when the server cannot be reached', async () => {
      api.listRepQuestions.mockResolvedValue({ questions: [question('o1')] });
      api.answerRepQuestion.mockRejectedValue(new ApiError(0, 'network_error'));
      await open();
      await fillIn('o1');

      click(repCopy.answer.submit);
      await settle();

      expect(itemsIn(OPEN)).toHaveLength(1);
      expect(field('o1', 'amount')?.value).toBe('350');
      expect(page().textContent).toContain(repCopy.answer.notSaved);
    });
  });

  describe('the /rep route', () => {
    it('opens the rep page, with no login in front of it', async () => {
      const route = routes.find((candidate) => candidate.path === 'rep');

      expect(route).toBeDefined();
      expect(route?.canActivate).toBeUndefined();
      expect(await route?.loadComponent?.()).toBe(RepPage);
    });
  });
});
