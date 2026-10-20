import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../../shared/api';
import {
  CarCandidateView,
  ConversationState,
  EmployeeProfile,
  PayComponent,
  ProfileChanges,
} from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { conversationState, EMPTY_PROFILE } from '../testing/conversation-state';
import { deferred } from '../testing/deferred';
import * as dom from './dom-events';
import { DetailsFieldName } from './field-values';
import { MyDetails } from './my-details';

const copy = employeeCopy.details;
const fields = copy.fields;
const payCopy = copy.payComponents;
const carCopy = copy.currentCar;

const COMMISSION: PayComponent = { kind: 'commission', label: 'עמלות משתנות', amount: '1000' };
const BONUS: PayComponent = { kind: 'bonus', label: 'בונוס שנתי', amount: '2500' };

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

const SCRIPT_ORDER = [
  fields.eligibility_group.label,
  fields.base_salary.label,
  fields.pay_components.label,
  fields.pension_rate_percent.label,
  fields.study_fund_rate_percent.label,
  fields.credit_points.label,
  fields.has_current_car.label,
  fields.current_car.label,
  fields.current_car_participation.label,
  fields.occupation.label,
  fields.children_under_23.label,
  fields.display_name.label,
];

function stateWith(changes: Partial<EmployeeProfile>, id = 'c1'): ConversationState {
  return conversationState(id, { profile: { ...EMPTY_PROFILE, ...changes } });
}

function createFakeApi() {
  return {
    patchProfile: vi.fn<ApiClient['patchProfile']>(),
    searchCars: vi.fn<ApiClient['searchCars']>(),
  };
}

describe('MyDetails', () => {
  let api: ReturnType<typeof createFakeApi>;
  let store: EmployeeStore;
  let fixture: ComponentFixture<MyDetails>;

  beforeEach(() => {
    api = createFakeApi();
    api.patchProfile.mockResolvedValue(conversationState('c1'));
    TestBed.configureTestingModule({
      providers: [EmployeeStore, { provide: ApiClient, useValue: api }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  async function open(state: ConversationState = conversationState('c1')): Promise<void> {
    store.show(state);
    fixture = TestBed.createComponent(MyDetails);
    await fixture.whenStable();
  }

  function all<T extends Element>(selector: string): T[] {
    return Array.from((fixture.nativeElement as HTMLElement).querySelectorAll<T>(selector));
  }

  function control(name: DetailsFieldName): HTMLInputElement | HTMLSelectElement {
    const [element] = all<HTMLInputElement | HTMLSelectElement>(`#details-${name}`);
    return element;
  }

  function input(name: DetailsFieldName): HTMLInputElement {
    return control(name) as HTMLInputElement;
  }

  // The text next to a field, which its aria-describedby points at while the text is showing.
  function messageOf(name: DetailsFieldName): string | null {
    const message = control(name).closest('app-details-field')?.querySelector('.field-error');
    if (message === null || message === undefined) {
      return null;
    }
    expect(control(name).getAttribute('aria-describedby')).toBe(message.id);
    return message.textContent?.trim() ?? null;
  }

  function type(name: DetailsFieldName, text: string): void {
    const element = input(name);
    element.value = text;
    element.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function choose(name: DetailsFieldName, value: string): void {
    const element = control(name);
    element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function leave(name: DetailsFieldName): void {
    control(name).dispatchEvent(new Event('blur'));
  }

  function pressEnter(name: DetailsFieldName): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    control(name).dispatchEvent(event);
    return event;
  }

  // The panel saves through promises that the test cannot await, so it waits for them to end.
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

  // Each row of the panel starts with its title: the label of a field, or the legend of an editor.
  function titles(): (string | undefined)[] {
    const rows = Array.from((fixture.nativeElement as HTMLElement).children);
    return rows.map((row) => row.querySelector('label, legend')?.textContent?.trim());
  }

  function textOf(selector: string): string | undefined {
    return all(selector)[0]?.textContent?.trim();
  }

  describe('the fields', () => {
    it('are listed in the order of the script, filled or empty, each with a label', async () => {
      await open();

      expect(titles()).toEqual(SCRIPT_ORDER);
      for (const label of all<HTMLLabelElement>('label')) {
        expect(label.control).not.toBeNull();
      }
    });

    it('are empty for a conversation without data', async () => {
      await open();

      for (const element of all<HTMLInputElement | HTMLSelectElement>('input, select')) {
        expect(element.value).toBe('');
      }
      expect(textOf('app-pay-components .empty')).toBe(copy.notEntered);
      expect(textOf('app-current-car .empty')).toBe(copy.notEntered);
    });

    it('show the stored values', async () => {
      await open(
        stateWith({
          display_name: 'דנה',
          eligibility_group: 'C',
          base_salary: '14200',
          pay_components: [{ kind: 'commission', label: 'עמלות משתנות', amount: '1000' }],
          pension_rate_percent: '6',
          study_fund_rate_percent: '2.5',
          credit_points: '2.25',
          occupation: 'מורה',
          children_under_23: 2,
          has_current_car: true,
          current_car: { manufacturer: 'טויוטה', model: 'קורולה', registration_year: 2020 },
          current_car_key: '2026-2020-1-01229-00001',
          current_car_participation: '350',
        }),
      );

      const shown = Object.fromEntries(
        all<HTMLInputElement | HTMLSelectElement>('input, select').map((element) => [
          element.id,
          element.value,
        ]),
      );

      expect(shown).toEqual({
        'details-eligibility_group': 'C',
        'details-base_salary': '14200',
        'details-pension_rate_percent': '6',
        'details-study_fund_rate_percent': '2.5',
        'details-credit_points': '2.25',
        'details-has_current_car': 'yes',
        'details-current_car_participation': '350',
        'details-occupation': 'מורה',
        'details-children_under_23': '2',
        'details-display_name': 'דנה',
        'details-pay-component-0-kind': 'commission',
        'details-pay-component-0-label': 'עמלות משתנות',
        'details-pay-component-0-amount': '1000',
      });
      expect(textOf('app-current-car .car-name')).toBe('טויוטה קורולה, 2020');
      expect(textOf('app-current-car .key-value')).toBe('2026-2020-1-01229-00001');
    });

    it('use selects for the choices and number keyboards for the numbers', async () => {
      await open();

      const group = control('eligibility_group') as HTMLSelectElement;
      expect(Array.from(group.options, (option) => option.textContent?.trim())).toEqual([
        copy.choices.noGroup,
        'א',
        'ב',
        'ג',
        'ד',
      ]);
      expect(control('has_current_car').tagName).toBe('SELECT');
      expect(input('base_salary').getAttribute('inputmode')).toBe('decimal');
      expect(input('credit_points').getAttribute('inputmode')).toBe('decimal');
      expect(input('children_under_23').getAttribute('inputmode')).toBe('numeric');
      expect(input('occupation').hasAttribute('inputmode')).toBe(false);
    });
  });

  describe('an invalid value', () => {
    // The group is a select, so the test adds the option that the page itself would never offer.
    function enterGroup(value: string): void {
      (control('eligibility_group') as HTMLSelectElement).append(new Option(value, value));
      choose('eligibility_group', value);
    }

    it.each<[DetailsFieldName, string, string]>([
      ['eligibility_group', 'E', copy.errors.group],
      ['base_salary', '-1', copy.errors.amount],
      ['pension_rate_percent', '101', copy.errors.percent],
      ['credit_points', '-1', copy.errors.points],
      ['children_under_23', '2.5', copy.errors.count],
      ['study_fund_rate_percent', 'abc', copy.errors.percent],
    ])('is rejected next to the field, and not sent: %s %j', async (name, text, message) => {
      await open(stateWith({ eligibility_group: 'B' }));
      if (name === 'eligibility_group') {
        enterGroup(text);
      } else {
        type(name, text);
      }

      leave(name);
      await settle();

      expect(control(name).getAttribute('aria-invalid')).toBe('true');
      expect(messageOf(name)).toBe(message);
      expect(api.patchProfile).not.toHaveBeenCalled();
      expect(store.state()?.profile.eligibility_group).toBe('B');
      expect(store.notice()).toBeNull();
    });

    it('is not sent on Enter either, and the text stays', async () => {
      await open();
      type('base_salary', 'abc');

      const enter = pressEnter('base_salary');
      await settle();

      expect(enter.defaultPrevented).toBe(true);
      expect(messageOf('base_salary')).toBe(copy.errors.amount);
      expect(input('base_salary').value).toBe('abc');
      expect(api.patchProfile).not.toHaveBeenCalled();
    });

    it('shows its message only once the field was left, and drops it when the value is fixed', async () => {
      await open();
      type('base_salary', '-');
      await settle();
      expect(messageOf('base_salary')).toBeNull();

      leave('base_salary');
      await settle();
      expect(messageOf('base_salary')).toBe(copy.errors.amount);

      type('base_salary', '16000');
      await settle();
      expect(messageOf('base_salary')).toBeNull();
      expect(control('base_salary').hasAttribute('aria-invalid')).toBe(false);
    });
  });

  describe('a valid value', () => {
    it('is saved when the field is left, and the field then shows the stored value', async () => {
      api.patchProfile.mockResolvedValue(
        stateWith({ eligibility_group: 'C', base_salary: '16000' }),
      );
      await open(stateWith({ eligibility_group: 'C', base_salary: '14200' }));
      type('base_salary', '16,000');

      leave('base_salary');
      await settle();

      expect(api.patchProfile).toHaveBeenCalledOnce();
      expect(api.patchProfile).toHaveBeenCalledWith('c1', { base_salary: '16000' });
      expect(input('base_salary').value).toBe('16000');
      expect(messageOf('base_salary')).toBeNull();
      expect(control('base_salary').hasAttribute('aria-invalid')).toBe(false);
    });

    it('is saved on Enter, and the key does not reach a form around the panel', async () => {
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '15000');

      const enter = pressEnter('base_salary');
      await settle();

      expect(enter.defaultPrevented).toBe(true);
      expect(api.patchProfile).toHaveBeenCalledWith('c1', { base_salary: '15000' });
    });

    it.each<[DetailsFieldName, string, ProfileChanges]>([
      ['eligibility_group', 'D', { eligibility_group: 'D' }],
      ['pension_rate_percent', '6.5', { pension_rate_percent: '6.5' }],
      ['study_fund_rate_percent', '100', { study_fund_rate_percent: '100' }],
      ['credit_points', '2.25', { credit_points: '2.25' }],
      ['has_current_car', 'no', { has_current_car: false }],
      ['current_car_participation', '350', { current_car_participation: '350' }],
      ['children_under_23', '3', { children_under_23: 3 }],
      ['occupation', ' מורה ', { occupation: 'מורה' }],
    ])('sends %s as the server reads it: %j', async (name, text, change) => {
      await open();
      if (control(name).tagName === 'SELECT') {
        choose(name, text);
      } else {
        type(name, text);
      }

      leave(name);
      await settle();

      expect(api.patchProfile).toHaveBeenCalledOnce();
      expect(api.patchProfile).toHaveBeenCalledWith('c1', change);
    });

    it('sends nothing when the text means the value that is stored', async () => {
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', ' 14,200 ');

      leave('base_salary');
      await settle();

      expect(api.patchProfile).not.toHaveBeenCalled();
      expect(input('base_salary').value).toBe('14200');
    });

    it('clears a field that is emptied', async () => {
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '');

      leave('base_salary');
      await settle();

      expect(api.patchProfile).toHaveBeenCalledWith('c1', { base_salary: null });
    });
  });

  describe('a save that fails', () => {
    it('keeps the typed value and says next to the field that it was not saved', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '17000');

      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(copy.notSaved));

      expect(input('base_salary').value).toBe('17000');
      expect(control('base_salary').hasAttribute('aria-invalid')).toBe(false);
      expect(store.notice()).toBe('change_not_saved');
      expect(store.state()?.profile.base_salary).toBe('14200');
    });

    it('can be tried again with the same value, and the message goes when it works', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(500, 'unexpected_response'));
      api.patchProfile.mockResolvedValueOnce(stateWith({ base_salary: '17000' }));
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '17000');
      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(copy.notSaved));

      pressEnter('base_salary');
      await until(() => expect(messageOf('base_salary')).toBeNull());

      expect(api.patchProfile).toHaveBeenCalledTimes(2);
      expect(store.notice()).toBeNull();
      expect(input('base_salary').value).toBe('17000');
    });

    it('drops the message when the profile catches up with the text, for example through the chat', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '17000');
      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(copy.notSaved));

      store.state.set(stateWith({ base_salary: '17000' }));
      await settle();

      expect(messageOf('base_salary')).toBeNull();
    });

    it('keeps the message of one field while another field is saved', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      api.patchProfile.mockResolvedValueOnce(
        stateWith({ base_salary: '14200', pension_rate_percent: '6' }),
      );
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '17000');
      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(copy.notSaved));

      type('pension_rate_percent', '6');
      leave('pension_rate_percent');
      await settle();

      expect(messageOf('base_salary')).toBe(copy.notSaved);
      expect(input('base_salary').value).toBe('17000');
    });

    it('drops the message when the employee changes the text', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '17000');
      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(copy.notSaved));

      type('base_salary', '17001');
      await settle();

      expect(messageOf('base_salary')).toBeNull();
    });
  });

  describe('a rejection by the server', () => {
    it('shows the message of the server next to its field, like an invalid value', async () => {
      const rejection = 'הסכום גבוה מדי.';
      api.patchProfile.mockRejectedValueOnce(
        new ApiError(422, 'invalid_fields', [{ field: 'base_salary', message: rejection }]),
      );
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '99999999');

      leave('base_salary');
      await until(() => expect(messageOf('base_salary')).toBe(rejection));

      expect(control('base_salary').getAttribute('aria-invalid')).toBe('true');
      expect(input('base_salary').value).toBe('99999999');
      expect(store.notice()).toBeNull();
    });
  });

  describe('when the stored profile reloads', () => {
    it('keeps the text of a field being changed and updates the others', async () => {
      await open(stateWith({ base_salary: '14200', pension_rate_percent: '6' }));
      type('base_salary', '15');

      store.state.set(stateWith({ base_salary: '14200', pension_rate_percent: '7' }));
      await settle();

      expect(input('base_salary').value).toBe('15');
      expect(input('pension_rate_percent').value).toBe('7');
    });

    it('shows another conversation in full', async () => {
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '15');

      store.show(stateWith({ base_salary: '20000', occupation: 'נהג' }, 'c2'));
      await settle();

      expect(input('base_salary').value).toBe('20000');
      expect(input('occupation').value).toBe('נהג');
    });
  });

  describe('saves that follow each other', () => {
    it('send a field once when it is left right after Enter', async () => {
      const pending = deferred<ConversationState>();
      api.patchProfile.mockReturnValue(pending.promise);
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '16000');

      pressEnter('base_salary');
      leave('base_salary');
      pending.resolve(stateWith({ base_salary: '16000' }));
      await settle();

      expect(api.patchProfile).toHaveBeenCalledOnce();
      expect(input('base_salary').value).toBe('16000');
    });

    it('keep text typed while a save is under way, and send it when the field is left', async () => {
      const pending = deferred<ConversationState>();
      api.patchProfile.mockReturnValueOnce(pending.promise);
      await open(stateWith({ base_salary: '14200' }));
      type('base_salary', '16000');
      pressEnter('base_salary');
      await settle();

      type('base_salary', '16500');
      pending.resolve(stateWith({ base_salary: '16000' }));
      await settle();
      expect(input('base_salary').value).toBe('16500');

      api.patchProfile.mockResolvedValueOnce(stateWith({ base_salary: '16500' }));
      leave('base_salary');
      await settle();

      expect(api.patchProfile).toHaveBeenLastCalledWith('c1', { base_salary: '16500' });
      expect(input('base_salary').value).toBe('16500');
    });

    it('drop a save that was waiting when another conversation was opened', async () => {
      const pending = deferred<ConversationState>();
      api.patchProfile.mockReturnValueOnce(pending.promise);
      await open();
      type('base_salary', '16000');
      leave('base_salary');
      type('pension_rate_percent', '6');
      leave('pension_rate_percent');
      await settle();

      store.show(stateWith({}, 'c2'));
      await settle();
      type('pension_rate_percent', '6');
      pending.resolve(stateWith({ base_salary: '16000' }));
      await settle();

      expect(api.patchProfile).toHaveBeenCalledOnce();
      expect(input('pension_rate_percent').value).toBe('6');
    });
  });

  // The server answers a change with the profile as it is after the change.
  function serveChanges(): void {
    api.patchProfile.mockImplementation((id, changes) =>
      Promise.resolve(
        conversationState(id, {
          profile: { ...(store.state()?.profile ?? EMPTY_PROFILE), ...changes },
        }),
      ),
    );
  }

  function panel(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function labelled(root: ParentNode, label: string): HTMLInputElement {
    return dom.controlLabelled(root, label) as HTMLInputElement;
  }

  describe('the pay components', () => {
    it('are saved as a list when a component is added or removed', async () => {
      serveChanges();
      await open(stateWith({ pay_components: [COMMISSION] }));

      dom.buttonNamed(panel(), payCopy.add).click();
      await settle();
      const [, added] = all<HTMLElement>('app-pay-components li');
      dom.selectOption(
        dom.controlLabelled(added, payCopy.fields.kind.label) as HTMLSelectElement,
        'bonus',
      );
      dom.typeInto(labelled(added, payCopy.fields.label.label), BONUS.label);
      dom.typeInto(labelled(added, payCopy.fields.amount.label), '2500');
      dom.leave(labelled(added, payCopy.fields.amount.label));
      await settle();

      expect(api.patchProfile).toHaveBeenLastCalledWith('c1', {
        pay_components: [COMMISSION, BONUS],
      });

      dom.buttonNamed(panel(), `${payCopy.remove}: רכיב שכר 1`).click();
      await settle();

      expect(api.patchProfile).toHaveBeenLastCalledWith('c1', { pay_components: [BONUS] });
      expect(store.state()?.profile.pay_components).toEqual([BONUS]);
      expect(all('app-pay-components li')).toHaveLength(1);
    });

    it('can be answered with none, which the script counts as an answer', async () => {
      serveChanges();
      await open();

      dom.buttonNamed(panel(), payCopy.none).click();
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', { pay_components: [] });
      expect(store.state()?.profile.pay_components).toEqual([]);
    });
  });

  describe('the current car', () => {
    async function searchFor(candidates: CarCandidateView[]): Promise<void> {
      api.searchCars.mockResolvedValue({
        candidates,
        selection_required: candidates.length > 1,
        none_found: candidates.length === 0,
      });
      dom.buttonNamed(panel(), carCopy.choose).click();
      await settle();
      dom.typeInto(labelled(panel(), carCopy.year), '2020');
      dom.typeInto(labelled(panel(), carCopy.text), 'MODEL 3');
      dom.buttonNamed(panel(), carCopy.search).click();
      await settle();
    }

    it('is saved by its key when the employee chooses a candidate of the search', async () => {
      serveChanges();
      await open();
      await searchFor([MODEL_3]);

      all<HTMLInputElement>('app-current-car input[type="radio"]')[0].click();
      await settle();
      expect(api.patchProfile).not.toHaveBeenCalled();
      dom.buttonNamed(panel(), carCopy.confirm).click();
      await settle();

      expect(api.patchProfile).toHaveBeenCalledOnce();
      expect(api.patchProfile).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ current_car_key: MODEL_3.car_key }),
      );
      expect(store.state()?.profile.current_car_key).toBe(MODEL_3.car_key);
      expect(textOf('app-current-car .key-value')).toBe(MODEL_3.car_key);
    });

    it('shows the none-found message for a search with no match', async () => {
      await open();

      await searchFor([]);

      expect(textOf('app-current-car [aria-live="polite"]')).toBe(carCopy.noneFound);
      expect(all('app-current-car input[type="radio"]')).toHaveLength(0);
      expect(api.patchProfile).not.toHaveBeenCalled();
    });
  });
});
