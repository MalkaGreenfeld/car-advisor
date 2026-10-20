import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../../shared/api';
import {
  CarCandidateView,
  CarSearchResponse,
  ConversationState,
  EmployeeProfile,
} from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { conversationState, EMPTY_PROFILE } from '../testing/conversation-state';
import { deferred } from '../testing/deferred';
import { buttonNamed, controlLabelled, typeInto } from './dom-events';
import { CurrentCar } from './current-car';
import { SaveQueue } from './save-queue';

const copy = employeeCopy.details.currentCar;
const details = copy.details;

const MODEL_3: CarCandidateView = {
  car_key: '2026-2020-1-01229-00001',
  manufacturer_name: "טסלה ארה''ב",
  trade_name: 'MODEL 3',
  model_code: '00001',
  model_description: '3E7E',
  vehicle_type_code: '1',
  engine_cc: 0,
  powertrain: 'electric',
  list_price: 322200,
};
const MODEL_3_LONG_RANGE: CarCandidateView = {
  ...MODEL_3,
  car_key: '2026-2020-1-01229-00002',
  model_code: '00002',
  list_price: 310560,
};

function found(...candidates: CarCandidateView[]): CarSearchResponse {
  return {
    candidates,
    selection_required: candidates.length > 1,
    none_found: candidates.length === 0,
  };
}

function stateWith(changes: Partial<EmployeeProfile>, id = 'c1'): ConversationState {
  return conversationState(id, { profile: { ...EMPTY_PROFILE, ...changes } });
}

describe('CurrentCar', () => {
  let api: {
    patchProfile: ReturnType<typeof vi.fn<ApiClient['patchProfile']>>;
    searchCars: ReturnType<typeof vi.fn<ApiClient['searchCars']>>;
  };
  let store: EmployeeStore;
  let fixture: ComponentFixture<CurrentCar>;

  beforeEach(() => {
    api = {
      patchProfile: vi.fn<ApiClient['patchProfile']>(),
      searchCars: vi.fn<ApiClient['searchCars']>(),
    };
    api.searchCars.mockResolvedValue(found(MODEL_3, MODEL_3_LONG_RANGE));
    // Answers like the server does: with the profile as it is after the change.
    api.patchProfile.mockImplementation((id, changes) =>
      Promise.resolve(
        conversationState(id, {
          profile: { ...(store.state()?.profile ?? EMPTY_PROFILE), ...changes },
        }),
      ),
    );
    TestBed.configureTestingModule({
      providers: [EmployeeStore, SaveQueue, { provide: ApiClient, useValue: api }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  async function open(changes: Partial<EmployeeProfile> = {}): Promise<void> {
    store.show(stateWith(changes));
    fixture = TestBed.createComponent(CurrentCar);
    await fixture.whenStable();
  }

  const root = (): HTMLElement => fixture.nativeElement as HTMLElement;

  function text(selector: string): string | undefined {
    return root().querySelector(selector)?.textContent?.trim();
  }

  function input(label: string): HTMLInputElement {
    return controlLabelled(root(), label) as HTMLInputElement;
  }

  function radios(): HTMLInputElement[] {
    return Array.from(root().querySelectorAll<HTMLInputElement>('input[type="radio"]'));
  }

  // What a screen reader reads out: the polite live region.
  const announced = (): string | undefined => text('[aria-live="polite"]');

  function errorOf(control: Element): string | null {
    const id = control.getAttribute('aria-describedby');
    return (
      (id === null ? null : root().querySelector(`#${id}.field-error`))?.textContent?.trim() ?? null
    );
  }

  async function settle(): Promise<void> {
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }

  function until(assertion: () => void): Promise<void> {
    return vi.waitFor(() => {
      fixture.detectChanges();
      assertion();
    });
  }

  async function openSearch(): Promise<void> {
    root().querySelector('button')?.click();
    await settle();
  }

  async function search(year = '2020', name = 'טסלה'): Promise<void> {
    typeInto(input(copy.year), year);
    typeInto(input(copy.text), name);
    buttonNamed(root(), copy.search).click();
    await settle();
  }

  describe('the car', () => {
    it('shows that nothing was entered, and offers to choose a car', async () => {
      await open();

      expect(text('.empty')).toBe(employeeCopy.details.notEntered);
      expect(root().querySelector('button')?.textContent?.trim()).toBe(copy.choose);
    });

    it('shows the name and the Official_Key, and offers to change the car', async () => {
      await open({
        current_car: { manufacturer: 'טויוטה', model: 'קורולה', registration_year: 2020 },
        current_car_key: '2026-2020-1-01229-00001',
      });

      expect(text('.car-name')).toBe('טויוטה קורולה, 2020');
      expect(text('.key-label')).toBe(copy.keyLabel);
      expect(text('.key-value')).toBe('2026-2020-1-01229-00001');
      const opener = root().querySelector('button');
      expect(opener?.textContent?.trim()).toBe(copy.change);
      expect(opener?.getAttribute('aria-label')).toBe(
        `${copy.change}: ${employeeCopy.details.fields.current_car.label}`,
      );
    });

    it('says that the key is missing for a car that was only described', async () => {
      await open({
        current_car: { manufacturer: 'טויוטה', model: 'קורולה', registration_year: 2020 },
      });

      expect(text('.key-label')).toBe(copy.keyLabel);
      expect(text('.key-value')).toBe(employeeCopy.details.notEntered);
    });
  });

  describe('the search', () => {
    it('opens inline with a labelled year and text, and the year takes the focus', async () => {
      await open();

      await openSearch();

      const opener = root().querySelector('button');
      expect(opener?.getAttribute('aria-expanded')).toBe('true');
      expect(input(copy.year).getAttribute('inputmode')).toBe('numeric');
      expect(input(copy.text)).toBeTruthy();
      expect(document.activeElement).toBe(input(copy.year));
    });

    it.each([
      ['a year with fewer than four digits', '20', 'טסלה', copy.errors.year, copy.year],
      ['a year that is not a number', 'abcd', 'טסלה', copy.errors.year, copy.year],
      ['text that is blank', '2020', '   ', copy.errors.text, copy.text],
    ])('is not run for %s', async (_, year, name, message, label) => {
      await open();
      await openSearch();
      typeInto(input(copy.year), year);
      typeInto(input(copy.text), name);

      buttonNamed(root(), copy.search).click();
      await settle();

      expect(api.searchCars).not.toHaveBeenCalled();
      expect(input(label).getAttribute('aria-invalid')).toBe('true');
      expect(errorOf(input(label))).toBe(message);
      expect(document.activeElement).toBe(input(label));
    });

    it('is run with the year as a number and the trimmed text, by the submit of the form', async () => {
      await open();
      await openSearch();
      typeInto(input(copy.year), '2020');
      typeInto(input(copy.text), ' טסלה ');

      input(copy.text).form?.requestSubmit();
      await settle();

      expect(api.searchCars).toHaveBeenCalledExactlyOnceWith(2020, 'טסלה');
    });

    it('is closed by Cancel without a save, and the focus goes back to the opener', async () => {
      await open();
      await openSearch();
      typeInto(input(copy.year), '2020');

      buttonNamed(root(), copy.cancel).click();
      await settle();

      expect(root().querySelector('form')).toBeNull();
      expect(document.activeElement).toBe(root().querySelector('button'));
      expect(root().querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
      expect(api.patchProfile).not.toHaveBeenCalled();

      await openSearch();
      expect(input(copy.year).value).toBe('');
      expect(input(copy.text).value).toBe('');
    });

    it('drops the answer to a search that was closed meanwhile', async () => {
      const pending = deferred<CarSearchResponse>();
      api.searchCars.mockReturnValueOnce(pending.promise);
      await open();
      await openSearch();
      await search();

      buttonNamed(root(), copy.cancel).click();
      await settle();
      pending.resolve(found(MODEL_3));
      await settle();

      expect(root().querySelector('form')).toBeNull();
      expect(radios()).toHaveLength(0);
    });
  });

  describe('the candidates', () => {
    it('are a radio group with the details of Req 2.6, announced with their number', async () => {
      api.searchCars.mockResolvedValue(
        found(MODEL_3, { ...MODEL_3_LONG_RANGE, model_description: '' }),
      );
      await open();
      await openSearch();

      await search();

      expect(announced()).toBe('נמצאו 2 רכבים. יש לבחור את הרכב שלך.');
      const group = root().querySelector('.candidates');
      expect(group?.tagName).toBe('FIELDSET');
      expect(group?.querySelector('legend')?.textContent?.trim()).toBe(copy.candidates);
      expect(radios()).toHaveLength(2);
      const linesOf = (radio: HTMLInputElement): (string | undefined)[] =>
        Array.from(radio.closest('li')?.querySelectorAll('.candidate-name, .fact') ?? [], (line) =>
          line.textContent?.trim(),
        );
      expect(linesOf(radios()[0])).toEqual([
        "טסלה ארה''ב MODEL 3",
        `${details.modelCode} 00001`,
        `${details.modelDescription} 3E7E`,
        `${details.vehicleType} 1`,
        `${details.engine} 0 ${details.engineUnit}`,
        `${details.powertrain} ${copy.powertrains.electric}`,
        `${details.listPrice} ₪322,200`,
        `${copy.keyLabel} 2026-2020-1-01229-00001`,
      ]);
      expect(linesOf(radios()[1])).not.toContain(`${details.modelDescription} `);
      expect(linesOf(radios()[1])).toHaveLength(7);
      for (const radio of radios()) {
        expect(radio.labels).toHaveLength(1);
      }
    });

    it('leave the car unidentified until the employee picks one, even a single candidate', async () => {
      api.searchCars.mockResolvedValue(found(MODEL_3));
      await open();
      await openSearch();

      await search();

      expect(announced()).toBe(copy.foundOne);
      expect(radios().map((radio) => radio.checked)).toEqual([false]);
      expect(buttonNamed(root(), copy.confirm).disabled).toBe(true);
      expect(api.patchProfile).not.toHaveBeenCalled();
    });

    it('are replaced by the result of a new search', async () => {
      await open();
      await openSearch();
      await search();
      radios()[1].click();
      await settle();

      api.searchCars.mockResolvedValueOnce(found(MODEL_3));
      await search('2020', 'MODEL 3');

      expect(radios()).toHaveLength(1);
      expect(radios()[0].checked).toBe(false);
    });
  });

  describe('choosing a candidate', () => {
    it('saves the key with the description of the car, shows the car and closes the search', async () => {
      await open({
        current_car: { manufacturer: 'טויוטה', model: 'קורולה', registration_year: 2019 },
      });
      await openSearch();
      await search();

      radios()[1].click();
      await settle();
      expect(buttonNamed(root(), copy.confirm).disabled).toBe(false);
      buttonNamed(root(), copy.confirm).click();
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', {
        current_car_key: '2026-2020-1-01229-00002',
        current_car: { manufacturer: "טסלה ארה''ב", model: 'MODEL 3', registration_year: 2020 },
      });
      expect(root().querySelector('form')).toBeNull();
      expect(text('.car-name')).toBe("טסלה ארה''ב MODEL 3, 2020");
      expect(text('.key-value')).toBe('2026-2020-1-01229-00002');
      expect(document.activeElement).toBe(root().querySelector('button'));
    });

    it('is not saved before the employee confirms the choice', async () => {
      await open();
      await openSearch();
      await search();

      radios()[0].click();
      await settle();

      expect(api.patchProfile).not.toHaveBeenCalled();
      expect(radios().map((radio) => radio.checked)).toEqual([true, false]);
    });

    it('keeps the search and the choice open when the save fails, and can be tried again', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open();
      await openSearch();
      await search();
      radios()[0].click();
      await settle();

      buttonNamed(root(), copy.confirm).click();
      await until(() => expect(text('.choose .field-error')).toBe(employeeCopy.details.notSaved));

      expect(radios().map((radio) => radio.checked)).toEqual([true, false]);
      expect(buttonNamed(root(), copy.confirm).getAttribute('aria-describedby')).toBe(
        root().querySelector('.choose .field-error')?.id,
      );
      expect(store.state()?.profile.current_car_key).toBeNull();
      expect(store.notice()).toBe('change_not_saved');

      buttonNamed(root(), copy.confirm).click();
      await until(() => expect(root().querySelector('form')).toBeNull());
      expect(store.state()?.profile.current_car_key).toBe(MODEL_3.car_key);
    });

    it('shows the message of the server when it rejects the key', async () => {
      const rejection = 'יש לבחור את הרכב מתוצאות החיפוש.';
      api.patchProfile.mockRejectedValueOnce(
        new ApiError(422, 'invalid_fields', [{ field: 'current_car_key', message: rejection }]),
      );
      await open();
      await openSearch();
      await search();
      radios()[0].click();
      await settle();

      buttonNamed(root(), copy.confirm).click();
      await until(() => expect(text('.choose .field-error')).toBe(rejection));

      expect(store.notice()).toBeNull();
    });
  });

  describe('a search that has no candidates', () => {
    it('says that none was found, in the live region', async () => {
      api.searchCars.mockResolvedValue(found());
      await open();
      await openSearch();

      await search('1999', 'אין כזה');

      expect(announced()).toBe(copy.noneFound);
      expect(radios()).toHaveLength(0);
      expect(root().querySelector('.candidates')).toBeNull();
    });

    it.each([
      ['the inputs are missing', new ApiError(422, 'missing_inputs'), copy.missingInputs],
      ['the lookup fails', new ApiError(503, 'lookup_failed'), copy.searchFailed],
      ['the network fails', new ApiError(0, 'network_error'), copy.searchFailed],
    ])('says so when %s, not that none was found', async (_, failure, message) => {
      api.searchCars.mockRejectedValue(failure);
      await open();
      await openSearch();

      await search();

      expect(announced()).toBe(message);
      expect(announced()).not.toBe(copy.noneFound);
      expect(radios()).toHaveLength(0);
    });
  });

  describe('another conversation', () => {
    it('closes the search and starts afresh', async () => {
      await open();
      await openSearch();
      await search();

      store.show(stateWith({}, 'c2'));
      await settle();

      expect(root().querySelector('form')).toBeNull();
      await openSearch();
      expect(input(copy.year).value).toBe('');
      expect(announced()).toBe('');
      expect(radios()).toHaveLength(0);
    });
  });
});
