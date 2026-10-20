import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../../shared/api';
import { ConversationState, PayComponent } from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { conversationState, EMPTY_PROFILE } from '../testing/conversation-state';
import { deferred } from '../testing/deferred';
import {
  buttonNamed,
  controlLabelled,
  leave,
  pressEnterIn,
  selectOption,
  typeInto,
} from './dom-events';
import { PayComponents } from './pay-components';
import { SaveQueue } from './save-queue';

const copy = employeeCopy.details.payComponents;
const fields = copy.fields;
const AMOUNT_ERROR = employeeCopy.details.errors.amount;

const COMMISSION: PayComponent = { kind: 'commission', label: 'עמלות משתנות', amount: '1000' };
const BONUS: PayComponent = { kind: 'bonus', label: 'בונוס שנתי', amount: '2500' };

function stateWith(list: PayComponent[] | null, id = 'c1'): ConversationState {
  return conversationState(id, { profile: { ...EMPTY_PROFILE, pay_components: list } });
}

describe('PayComponents', () => {
  let api: { patchProfile: ReturnType<typeof vi.fn<ApiClient['patchProfile']>> };
  let store: EmployeeStore;
  let fixture: ComponentFixture<PayComponents>;

  beforeEach(() => {
    api = { patchProfile: vi.fn<ApiClient['patchProfile']>() };
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

  async function open(list: PayComponent[] | null = null): Promise<void> {
    store.show(stateWith(list));
    fixture = TestBed.createComponent(PayComponents);
    await fixture.whenStable();
  }

  const root = (): HTMLElement => fixture.nativeElement as HTMLElement;

  function rows(): HTMLElement[] {
    return Array.from(root().querySelectorAll<HTMLElement>('li'));
  }

  function field(row: HTMLElement, label: string): HTMLInputElement | HTMLSelectElement {
    return controlLabelled(row, label);
  }

  function input(row: HTMLElement, label: string): HTMLInputElement {
    return field(row, label) as HTMLInputElement;
  }

  function select(row: HTMLElement, label: string): HTMLSelectElement {
    return field(row, label) as HTMLSelectElement;
  }

  function text(selector: string): string | undefined {
    return root().querySelector(selector)?.textContent?.trim();
  }

  // The message of a field is what its aria-describedby points at.
  function errorOf(control: Element): string | null {
    const id = control.getAttribute('aria-describedby');
    const message = id === null ? null : root().querySelector(`#${id}.field-error`);
    return message?.textContent?.trim() ?? null;
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

  // Adds a row and fills it in, ending with the amount, which is the last field of the row.
  async function addRow(component: PayComponent, amount = component.amount): Promise<void> {
    buttonNamed(root(), copy.add).click();
    await settle();
    const row = rows()[rows().length - 1];
    selectOption(select(row, fields.kind.label), component.kind);
    typeInto(input(row, fields.label.label), component.label);
    typeInto(input(row, fields.amount.label), amount);
    leave(field(row, fields.amount.label));
    await settle();
  }

  describe('without an answer', () => {
    it('offers to add a component or to say that there are none', async () => {
      await open();

      expect(text('.empty')).toBe(employeeCopy.details.notEntered);
      expect(rows()).toHaveLength(0);
      expect(buttonNamed(root(), copy.add)).toBeTruthy();
      expect(buttonNamed(root(), copy.none)).toBeTruthy();
    });

    it('saves an empty list for none, which is an answer, and then shows it', async () => {
      await open();

      buttonNamed(root(), copy.none).click();
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', { pay_components: [] });
      expect(text('.empty')).toBe(copy.none);
      expect(root().querySelectorAll('button')).toHaveLength(1);
      expect(document.activeElement).toBe(buttonNamed(root(), copy.add));
    });
  });

  describe('the rows', () => {
    it('show each component in a group of its own with a title and a remove button', async () => {
      await open([COMMISSION, BONUS]);

      expect(rows()).toHaveLength(2);
      const [first, second] = rows();
      expect(field(first, fields.kind.label).value).toBe('commission');
      expect(field(first, fields.label.label).value).toBe('עמלות משתנות');
      expect(field(first, fields.amount.label).value).toBe('1000');
      expect(field(second, fields.kind.label).value).toBe('bonus');
      const group = second.querySelector('[role="group"]');
      const title = root().querySelector(`#${group?.getAttribute('aria-labelledby')}`);
      expect(title?.textContent?.trim()).toBe('רכיב שכר 2');
      expect(buttonNamed(second, 'הסרה: רכיב שכר 2').textContent?.trim()).toBe(copy.remove);
      expect(text('.empty')).toBeUndefined();
    });

    it('follow the stored list while nothing is being edited, and another conversation', async () => {
      await open([COMMISSION]);

      store.state.set(stateWith([COMMISSION, BONUS]));
      await settle();
      expect(rows()).toHaveLength(2);

      store.show(stateWith([BONUS], 'c2'));
      await settle();
      expect(rows()).toHaveLength(1);
      expect(field(rows()[0], fields.label.label).value).toBe('בונוס שנתי');
    });

    it('keep a text being edited when the stored list reloads', async () => {
      await open([COMMISSION]);
      typeInto(input(rows()[0], fields.amount.label), '12');

      store.state.set(stateWith([COMMISSION, BONUS]));
      await settle();

      expect(rows()).toHaveLength(1);
      expect(field(rows()[0], fields.amount.label).value).toBe('12');
    });
  });

  describe('a new row', () => {
    it('takes the focus, and is saved with the list once it is complete and valid', async () => {
      await open([COMMISSION]);

      buttonNamed(root(), copy.add).click();
      await settle();
      const row = rows()[1];
      expect(document.activeElement).toBe(field(row, fields.kind.label));
      expect(row.querySelector('.hint')?.textContent?.trim()).toBe(copy.unsaved);

      selectOption(select(row, fields.kind.label), 'bonus');
      typeInto(input(row, fields.label.label), ' בונוס שנתי ');
      leave(field(row, fields.label.label));
      await settle();
      expect(api.patchProfile).not.toHaveBeenCalled();

      typeInto(input(row, fields.amount.label), '2,500');
      pressEnterIn(field(row, fields.amount.label));
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', {
        pay_components: [COMMISSION, BONUS],
      });
      expect(field(rows()[1], fields.amount.label).value).toBe('2500');
      expect(field(rows()[1], fields.label.label).value).toBe('בונוס שנתי');
      expect(root().querySelector('.hint')).toBeNull();
    });

    it('is dropped without a request when it is removed before it is saved', async () => {
      await open([COMMISSION]);
      buttonNamed(root(), copy.add).click();
      await settle();

      buttonNamed(root(), `${copy.remove}: רכיב שכר 2`).click();
      await settle();

      expect(rows()).toHaveLength(1);
      expect(api.patchProfile).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(buttonNamed(root(), copy.add));
    });

    it('is saved as the first component of a list that was never answered', async () => {
      await open();

      await addRow(BONUS);

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', { pay_components: [BONUS] });
    });
  });

  describe('an invalid value', () => {
    it.each([
      ['an amount below zero', fields.amount.label, '-1', AMOUNT_ERROR],
      ['an amount that is not a number', fields.amount.label, 'abc', AMOUNT_ERROR],
      ['a description left empty', fields.label.label, '  ', copy.errors.label],
    ])('is rejected next to its field, and not sent: %s', async (_, label, value, message) => {
      await open([COMMISSION]);
      const control = input(rows()[0], label);
      typeInto(control, value);

      leave(control);
      await settle();

      expect(control.getAttribute('aria-invalid')).toBe('true');
      expect(errorOf(control)).toBe(message);
      expect(control.value).toBe(value);
      expect(api.patchProfile).not.toHaveBeenCalled();
      expect(store.notice()).toBeNull();
    });

    it('is shown once the field is left, and goes when the value is fixed', async () => {
      await open([COMMISSION]);
      const control = input(rows()[0], fields.amount.label);
      typeInto(control, '-');
      await settle();
      expect(errorOf(control)).toBeNull();

      leave(control);
      await settle();
      expect(errorOf(control)).toBe(AMOUNT_ERROR);

      typeInto(control, '1200');
      await settle();
      expect(errorOf(control)).toBeNull();
      expect(control.hasAttribute('aria-invalid')).toBe(false);
    });

    it('is not sent with the other rows, which keep their saved values in the list', async () => {
      await open([COMMISSION, BONUS]);
      const [first, second] = rows();
      typeInto(input(first, fields.amount.label), 'abc');
      leave(field(first, fields.amount.label));
      typeInto(input(second, fields.amount.label), '3000');

      leave(field(second, fields.amount.label));
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', {
        pay_components: [COMMISSION, { ...BONUS, amount: '3000' }],
      });
      expect(field(rows()[0], fields.amount.label).value).toBe('abc');
      expect(errorOf(field(rows()[0], fields.amount.label))).toBe(AMOUNT_ERROR);
    });
  });

  describe('a component that is changed or removed', () => {
    it('is saved when the field is left, as part of the whole list', async () => {
      await open([COMMISSION, BONUS]);
      const label = input(rows()[0], fields.label.label);
      typeInto(label, 'עמלת מכירות');

      leave(label);
      await settle();

      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', {
        pay_components: [{ ...COMMISSION, label: 'עמלת מכירות' }, BONUS],
      });
    });

    it('is sent once when the field is left right after Enter', async () => {
      await open([COMMISSION]);
      const amount = input(rows()[0], fields.amount.label);
      typeInto(amount, '1100');

      const enter = pressEnterIn(amount);
      leave(amount);
      await settle();

      expect(enter.defaultPrevented).toBe(true);
      expect(api.patchProfile).toHaveBeenCalledExactlyOnceWith('c1', {
        pay_components: [{ ...COMMISSION, amount: '1100' }],
      });
    });

    it('is removed at once, and the last one leaves an empty list, not a missing one', async () => {
      await open([COMMISSION, BONUS]);

      buttonNamed(root(), `${copy.remove}: רכיב שכר 1`).click();
      await settle();
      expect(api.patchProfile).toHaveBeenLastCalledWith('c1', { pay_components: [BONUS] });
      expect(rows()).toHaveLength(1);

      buttonNamed(root(), `${copy.remove}: רכיב שכר 1`).click();
      await settle();
      expect(api.patchProfile).toHaveBeenLastCalledWith('c1', { pay_components: [] });
      expect(text('.empty')).toBe(copy.none);
      expect(root().querySelectorAll('button')).toHaveLength(1);
    });
  });

  describe('a save that fails', () => {
    it('keeps the typed value and says that it was not saved, until the next try works', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(0, 'network_error'));
      await open([COMMISSION]);
      const amount = input(rows()[0], fields.amount.label);
      typeInto(amount, '1100');
      leave(amount);
      await until(() => expect(text('.field-error')).toBe(employeeCopy.details.notSaved));

      expect(amount.value).toBe('1100');
      expect(root().querySelector('fieldset')?.getAttribute('aria-describedby')).toBe(
        root().querySelector('.field-error')?.id,
      );
      expect(store.notice()).toBe('change_not_saved');

      pressEnterIn(amount);
      await until(() => expect(text('.field-error')).toBeUndefined());
      expect(api.patchProfile).toHaveBeenCalledTimes(2);
      expect(store.notice()).toBeNull();
    });

    it('shows the message of the server when it rejects the list', async () => {
      const rejection = 'יש להזין לכל רכיב שכר סוג, תיאור וסכום.';
      api.patchProfile.mockRejectedValueOnce(
        new ApiError(422, 'invalid_fields', [{ field: 'pay_components', message: rejection }]),
      );
      await open();

      await addRow(BONUS);

      expect(text('.field-error')).toBe(rejection);
      expect(store.notice()).toBeNull();
      expect(rows()).toHaveLength(1);
    });

    it('drops the message when the employee changes a text', async () => {
      api.patchProfile.mockRejectedValueOnce(new ApiError(500, 'unexpected_response'));
      await open([COMMISSION]);
      const amount = input(rows()[0], fields.amount.label);
      typeInto(amount, '1100');
      leave(amount);
      await until(() => expect(text('.field-error')).toBe(employeeCopy.details.notSaved));

      typeInto(amount, '1101');
      await settle();

      expect(text('.field-error')).toBeUndefined();
    });
  });

  describe('saves in a row', () => {
    it('leave a response of an earlier conversation out of the one on screen', async () => {
      const pending = deferred<ConversationState>();
      api.patchProfile.mockReturnValueOnce(pending.promise);
      await open([COMMISSION]);
      typeInto(input(rows()[0], fields.amount.label), '1100');
      leave(field(rows()[0], fields.amount.label));
      await settle();

      store.show(stateWith([BONUS], 'c2'));
      await settle();
      pending.resolve(stateWith([{ ...COMMISSION, amount: '1100' }]));
      await settle();

      expect(rows()).toHaveLength(1);
      expect(field(rows()[0], fields.label.label).value).toBe('בונוס שנתי');
      expect(store.state()?.id).toBe('c2');
    });
  });
});
