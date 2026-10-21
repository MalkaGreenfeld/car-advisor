import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../../shared/api';
import { CatalogResponse, ConversationState, Group } from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import {
  alternativeView,
  conversationState,
  EMPTY_PROFILE,
  todayView,
} from '../testing/conversation-state';
import { deferred } from '../testing/deferred';
import { ComparisonTable } from './comparison-table';
import { comparisonCopy } from './comparison.copy';

const copy = comparisonCopy.picker;

const IONIQ = '2026-2026-1-01229-00004';
const EV6 = '2026-2026-1-02001-00007';
const OLD_CAR = '2019-2019-1-00011-00002';

const CATALOG: CatalogResponse = {
  cars: [
    { car_key: IONIQ, display_name: 'Hyundai Ioniq 5', group: 'C' },
    { car_key: EV6, display_name: 'Kia EV6', group: 'B' },
  ],
  missing_field: null,
};

function stateOf(
  group: Group | null,
  alternatives = [alternativeView(0)],
  id = 'c1',
): ConversationState {
  return conversationState(id, {
    // The group in use, as the occupation gives it.
    profile: EMPTY_PROFILE,
    employee_group: { group, source: group === null ? null : 'occupation' },
    comparison: { today: todayView(), alternatives },
  });
}

function changedTo(key: string, name: string, group: Group): ConversationState {
  return stateOf('C', [alternativeView(0, { car: { car_key: key, display_name: name, group } })]);
}

function createFakeApi() {
  const api = {
    getCatalog: vi.fn<ApiClient['getCatalog']>(),
    replaceAlternative: vi.fn<ApiClient['replaceAlternative']>(),
  };
  api.getCatalog.mockResolvedValue(CATALOG);
  return api;
}

describe('CarPicker', () => {
  let api: ReturnType<typeof createFakeApi>;
  let store: EmployeeStore;
  let fixture: ComponentFixture<ComparisonTable>;

  beforeEach(() => {
    api = createFakeApi();
    TestBed.configureTestingModule({
      providers: [EmployeeStore, { provide: ApiClient, useValue: api }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  async function open(state: ConversationState): Promise<void> {
    store.show(state);
    fixture = TestBed.createComponent(ComparisonTable);
    await settle();
  }

  async function settle(): Promise<void> {
    await fixture.whenStable();
    fixture.detectChanges();
  }

  // The catalog resource and the save work through promises that the test cannot await.
  function until(assertion: () => void): Promise<void> {
    return vi.waitFor(() => {
      fixture.detectChanges();
      assertion();
    });
  }

  function root(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function pickers(): HTMLSelectElement[] {
    return Array.from(root().querySelectorAll<HTMLSelectElement>('app-car-picker select'));
  }

  function picker(position = 0): HTMLSelectElement {
    return pickers()[position];
  }

  function optionTexts(select: HTMLSelectElement): string[] {
    return Array.from(select.options, (option) => option.textContent?.trim() ?? '');
  }

  function notes(): string[] {
    return Array.from(root().querySelectorAll('app-car-picker .note'), (note) =>
      (note.textContent ?? '').trim(),
    ).filter((text) => text !== '');
  }

  function choose(select: HTMLSelectElement, key: string): void {
    select.value = key;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  describe('the list', () => {
    it('offers the cars of the catalog as the display name and the group letter', async () => {
      await open(stateOf('C'));
      await until(() => expect(optionTexts(picker())).toHaveLength(2));

      expect(optionTexts(picker())).toEqual(['Hyundai Ioniq 5 · ג', 'Kia EV6 · ב']);
      expect(picker().value).toBe(IONIQ);
      expect(picker().disabled).toBe(false);
      expect(notes()).toEqual([]);
    });

    it('is loaded for the conversation, once for all the columns', async () => {
      await open(stateOf('C', [alternativeView(0), alternativeView(1)]));
      await until(() => expect(picker().disabled).toBe(false));

      expect(pickers()).toHaveLength(2);
      expect(api.getCatalog).toHaveBeenCalledExactlyOnceWith('c1');
    });

    it('is loaded again when the group changes, and not when the state is only refreshed', async () => {
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));

      store.state.set(stateOf('C'));
      await settle();
      expect(api.getCatalog).toHaveBeenCalledTimes(1);

      store.state.set(stateOf('D'));
      await until(() => expect(api.getCatalog).toHaveBeenCalledTimes(2));
    });

    it('keeps the car of the column as an option when the list does not hold it', async () => {
      const column = alternativeView(0, {
        car: { car_key: OLD_CAR, display_name: 'Skoda Octavia', group: 'D' },
      });
      await open(stateOf('C', [column]));
      await until(() => expect(picker().disabled).toBe(false));

      expect(optionTexts(picker())).toEqual([
        'Skoda Octavia · ד',
        'Hyundai Ioniq 5 · ג',
        'Kia EV6 · ב',
      ]);
      expect(picker().value).toBe(OLD_CAR);
    });

    it('has no picker for a column without a car', async () => {
      const waiver = alternativeView(1, { car: null });
      await open(stateOf('C', [alternativeView(0), waiver]));
      await until(() => expect(pickers()).toHaveLength(1));

      expect(root().querySelectorAll('app-car-picker')).toHaveLength(1);
    });

    it('is named after its column for a screen reader', async () => {
      await open(stateOf('C', [alternativeView(0), alternativeView(1)]));
      await until(() => expect(pickers()).toHaveLength(2));

      const names = pickers().map((select) => select.labels?.[0]?.textContent?.trim());

      expect(names).toEqual([
        `${copy.label} ${comparisonCopy.alternative} 1`,
        `${copy.label} ${comparisonCopy.alternative} 2`,
      ]);
    });
  });

  describe('a change', () => {
    it('sends the position of the column and the key of the car, and the table follows', async () => {
      api.replaceAlternative.mockResolvedValue(changedTo(EV6, 'Kia EV6', 'B'));
      await open(stateOf('C', [alternativeView(0), alternativeView(1)]));
      await until(() => expect(picker(1).disabled).toBe(false));

      choose(picker(1), EV6);

      await until(() => expect(store.state()?.alternatives).toBeDefined());
      expect(api.replaceAlternative).toHaveBeenCalledExactlyOnceWith('c1', 1, EV6);
      await until(() => expect(picker(0).value).toBe(EV6));
      expect(notes()).toEqual([]);
    });

    it('keeps the focus on the picker', async () => {
      api.replaceAlternative.mockResolvedValue(changedTo(EV6, 'Kia EV6', 'B'));
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));
      picker().focus();

      choose(picker(), EV6);
      await until(() => expect(picker().value).toBe(EV6));

      expect(document.activeElement).toBe(picker());
    });

    it('is ignored while the previous change is still being saved', async () => {
      const pending = deferred<ConversationState>();
      api.replaceAlternative.mockReturnValue(pending.promise);
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));

      choose(picker(), EV6);
      choose(picker(), IONIQ);
      choose(picker(), EV6);

      expect(api.replaceAlternative).toHaveBeenCalledOnce();
      pending.resolve(changedTo(EV6, 'Kia EV6', 'B'));
      await until(() => expect(picker().value).toBe(EV6));
    });

    it('does nothing when the car of the column is chosen again', async () => {
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));

      choose(picker(), IONIQ);

      expect(api.replaceAlternative).not.toHaveBeenCalled();
    });
  });

  describe('a change that fails', () => {
    it('keeps the previous car selected and says that it was not saved', async () => {
      api.replaceAlternative.mockRejectedValue(new ApiError(0, 'network_error'));
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));

      choose(picker(), EV6);

      await until(() => expect(notes()).toEqual([copy.notSaved]));
      expect(picker().value).toBe(IONIQ);
      expect(store.state()?.comparison?.alternatives[0].car?.car_key).toBe(IONIQ);
      expect(store.notice()).toBe('change_not_saved');
    });

    it('takes the message away with the next change that works', async () => {
      api.replaceAlternative.mockRejectedValueOnce(new ApiError(500, 'unexpected_response'));
      api.replaceAlternative.mockResolvedValueOnce(changedTo(EV6, 'Kia EV6', 'B'));
      await open(stateOf('C'));
      await until(() => expect(picker().disabled).toBe(false));
      choose(picker(), EV6);
      await until(() => expect(notes()).toEqual([copy.notSaved]));

      choose(picker(), EV6);

      await until(() => expect(picker().value).toBe(EV6));
      expect(notes()).toEqual([]);
    });
  });

  describe('when the list cannot be offered', () => {
    it('is disabled with a message while the group is unknown, and loads nothing (Req 11.18)', async () => {
      await open(stateOf(null));
      await settle();

      expect(picker().disabled).toBe(true);
      expect(notes()).toEqual([copy.needsGroup]);
      expect(picker().getAttribute('aria-describedby')).not.toBeNull();
      expect(optionTexts(picker())).toEqual(['Hyundai Ioniq 5 · ג']);
      expect(api.getCatalog).not.toHaveBeenCalled();
    });

    it('is disabled with the same message when the server names the group as missing', async () => {
      api.getCatalog.mockResolvedValue({ cars: [], missing_field: 'occupation' });
      await open(stateOf('C'));

      await until(() => expect(notes()).toEqual([copy.needsGroup]));
      expect(picker().disabled).toBe(true);
    });

    it('shows the car of the column and a message while the list loads', async () => {
      const pending = deferred<CatalogResponse>();
      api.getCatalog.mockReturnValue(pending.promise);
      // The app is not stable while the list loads, so this waits for the screen and not for it.
      store.show(stateOf('C'));
      fixture = TestBed.createComponent(ComparisonTable);
      await until(() => expect(notes()).toEqual([copy.loading]));

      expect(picker().disabled).toBe(true);
      expect(picker().value).toBe(IONIQ);

      pending.resolve(CATALOG);
      await until(() => expect(picker().disabled).toBe(false));
      expect(notes()).toEqual([]);
    });

    it('says so, and leaves the table as it is, when the list does not load', async () => {
      api.getCatalog.mockRejectedValue(new ApiError(503, 'database_unavailable'));
      await open(stateOf('C'));

      await until(() => expect(notes()).toEqual([copy.failed]));
      expect(picker().disabled).toBe(true);
      expect(root().querySelector('table')).not.toBeNull();
      expect(picker().value).toBe(IONIQ);
    });
  });
});
