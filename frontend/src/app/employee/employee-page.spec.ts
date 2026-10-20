import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { routes } from '../app.routes';
import { ApiClient, ApiError } from '../shared/api';
import { ConversationState } from '../shared/api-models';
import { sharedCopy } from '../shared/shared.copy';
import { comparisonCopy } from './comparison/comparison.copy';
import { EmployeeStore } from './employee-store';
import { employeeCopy } from './employee.copy';
import {
  alternativeView,
  conversationState,
  EMPTY_PROFILE,
  todayView,
} from './testing/conversation-state';
import { deferred } from './testing/deferred';

const NEW_CONVERSATION = employeeCopy.page.newConversation;

function createFakeApi() {
  const api = {
    createConversation: vi.fn<ApiClient['createConversation']>(),
    getConversation: vi.fn<ApiClient['getConversation']>(),
  };
  api.createConversation.mockResolvedValue(conversationState('new-id'));
  api.getConversation.mockImplementation((id) => Promise.resolve(conversationState(id)));
  return api;
}

describe('EmployeePage', () => {
  let api: ReturnType<typeof createFakeApi>;
  let router: Router;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    api = createFakeApi();
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes, withComponentInputBinding()),
        { provide: ApiClient, useValue: api },
      ],
    });
    router = TestBed.inject(Router);
    harness = await RouterTestingHarness.create();
  });

  function screen(): HTMLElement {
    return harness.routeNativeElement as HTMLElement;
  }

  function store(): EmployeeStore {
    return harness.routeDebugElement!.injector.get(EmployeeStore);
  }

  function textsOf(selector: string): (string | undefined)[] {
    return Array.from(screen().querySelectorAll(selector), (element) =>
      element.textContent?.trim(),
    );
  }

  function buttonsLabelled(label: string, region = 'main'): HTMLButtonElement[] {
    const buttons = screen().querySelectorAll<HTMLButtonElement>(`${region} button`);
    return Array.from(buttons).filter((button) => button.textContent?.trim() === label);
  }

  function click(label: string, region = 'main'): void {
    const matches = buttonsLabelled(label, region);
    expect(matches).toHaveLength(1);
    matches[0].click();
  }

  // The page works through promises that the test cannot await, so it renders and checks again
  // until the assertion holds.
  function until(assertion: () => void): Promise<void> {
    return vi.waitFor(() => {
      harness.detectChanges();
      assertion();
    });
  }

  function untilAreasShow(): Promise<void> {
    return until(() => expect(screen().querySelectorAll('h2')).toHaveLength(3));
  }

  describe('without a conversation ID', () => {
    it('creates a conversation and replaces the URL with its ID', async () => {
      const navigate = vi.spyOn(router, 'navigate');

      await harness.navigateByUrl('/employee');
      await until(() => expect(router.url).toBe('/employee/new-id'));

      expect(api.createConversation).toHaveBeenCalledOnce();
      expect(navigate).toHaveBeenCalledWith(['/employee', 'new-id'], { replaceUrl: true });
    });

    it('is where the root address redirects to', async () => {
      await harness.navigateByUrl('/');
      await until(() => expect(router.url).toBe('/employee/new-id'));
      expect(api.createConversation).toHaveBeenCalledOnce();
    });

    it('shows the failed message with a retry button when no conversation is created', async () => {
      api.createConversation.mockRejectedValueOnce(new ApiError(0, 'network_error'));

      await harness.navigateByUrl('/employee');
      await until(() => expect(screen().textContent).toContain(sharedCopy.failed));
      click(employeeCopy.page.retry);

      await until(() => expect(router.url).toBe('/employee/new-id'));
      expect(api.createConversation).toHaveBeenCalledTimes(2);
    });
  });

  describe('with a conversation ID', () => {
    it('loads the stored state as it is and shows the areas', async () => {
      const stored = conversationState('c1', {
        messages: [
          {
            id: 1,
            role: 'employee',
            text: 'שלום',
            notice: null,
            created_at: '2026-10-13T09:12:03Z',
          },
        ],
        profile: { ...EMPTY_PROFILE, eligibility_group: 'B', base_salary: '16000' },
        alternatives: [{ kind: 'waiver' }],
      });
      api.getConversation.mockResolvedValue(stored);

      await harness.navigateByUrl('/employee/c1');
      await untilAreasShow();

      expect(api.getConversation).toHaveBeenCalledWith('c1');
      expect(api.createConversation).not.toHaveBeenCalled();
      expect(store().state()).toBe(stored);
    });

    it('shows the loading message, with a new conversation action, until the state arrives', async () => {
      const pending = deferred<ConversationState>();
      api.getConversation.mockReturnValue(pending.promise);

      await harness.navigateByUrl('/employee/c1');
      await until(() => expect(screen().textContent).toContain(sharedCopy.loading));

      expect(screen().querySelector('h2')).toBeNull();
      expect(buttonsLabelled(NEW_CONVERSATION)).toHaveLength(1);
    });

    it('ignores the late response of a conversation that the URL has left', async () => {
      const slow = deferred<ConversationState>();
      api.getConversation.mockImplementation((id) =>
        id === 'slow' ? slow.promise : Promise.resolve(conversationState(id)),
      );

      await harness.navigateByUrl('/employee/slow');
      await harness.navigateByUrl('/employee/fast');
      await untilAreasShow();
      slow.resolve(conversationState('slow'));
      await new Promise((resolve) => setTimeout(resolve));

      expect(store().state()?.id).toBe('fast');
    });

    it('shows the failed message with a retry button for any other failure', async () => {
      api.getConversation.mockRejectedValueOnce(new ApiError(500, 'unexpected_response'));

      await harness.navigateByUrl('/employee/c1');
      await until(() => expect(screen().textContent).toContain(sharedCopy.failed));
      expect(buttonsLabelled(NEW_CONVERSATION)).toHaveLength(1);
      click(employeeCopy.page.retry);

      await untilAreasShow();
      expect(api.getConversation).toHaveBeenCalledTimes(2);
    });
  });

  describe('when the conversation does not exist', () => {
    beforeEach(async () => {
      api.getConversation.mockImplementation((id) =>
        id === 'missing'
          ? Promise.reject(new ApiError(404, 'conversation_not_found'))
          : Promise.resolve(conversationState(id)),
      );
      await harness.navigateByUrl('/employee/missing');
      await until(() => expect(screen().textContent).toContain(employeeCopy.page.notFound));
    });

    it('shows "not found" with a new conversation button, and creates nothing yet', () => {
      expect(buttonsLabelled(NEW_CONVERSATION, '.status')).toHaveLength(1);
      expect(buttonsLabelled(NEW_CONVERSATION, 'header')).toHaveLength(1);
      expect(screen().querySelector('h2')).toBeNull();
      expect(api.createConversation).not.toHaveBeenCalled();
    });

    it('creates a conversation and opens it when the button is used', async () => {
      click(NEW_CONVERSATION, '.status');

      await until(() => expect(router.url).toBe('/employee/new-id'));
      await untilAreasShow();
      expect(api.createConversation).toHaveBeenCalledOnce();
      expect(store().state()?.id).toBe('new-id');
    });
  });

  describe('when the path of the conversation matches no route of the server', () => {
    it('shows "not found" for a 404 without a code too', async () => {
      api.getConversation.mockRejectedValue(new ApiError(404, 'unexpected_response'));

      await harness.navigateByUrl('/employee/a%2Fb%3Fc');

      await until(() => expect(screen().textContent).toContain(employeeCopy.page.notFound));
      expect(screen().textContent).not.toContain(sharedCopy.failed);
    });

    it('still shows "failed" for another error', async () => {
      api.getConversation.mockRejectedValue(new ApiError(503, 'database_unavailable'));

      await harness.navigateByUrl('/employee/c1');

      await until(() => expect(screen().textContent).toContain(sharedCopy.failed));
      expect(screen().textContent).not.toContain(employeeCopy.page.notFound);
    });
  });

  describe('once the conversation has loaded', () => {
    beforeEach(async () => {
      await harness.navigateByUrl('/employee/c1');
      await untilAreasShow();
    });

    it('says that the update failed, keeps the screen as it is, and clears the message at the next success', async () => {
      const shown = store().state();
      api.getConversation.mockRejectedValueOnce(new ApiError(0, 'network_error'));

      await store().refresh();
      await until(() =>
        expect(textsOf('.update-failed')).toEqual([employeeCopy.page.updateFailed]),
      );
      expect(store().state()).toBe(shown);
      expect(screen().querySelectorAll('h2')).toHaveLength(3);

      await store().refresh();
      await until(() => expect(screen().querySelector('.update-failed')).toBeNull());
    });

    it('can be operated from the keyboard: every control is native, and no tab order is forced', () => {
      const controls = screen().querySelectorAll('button, input, select, textarea, a[href]');
      expect(controls.length).toBeGreaterThan(10);

      const clickable = screen().querySelectorAll('[tabindex], [role="button"], [onclick]');
      for (const element of Array.from(clickable)) {
        // Only the scrolling regions are focusable by tabindex, and they are labelled.
        expect(element.getAttribute('tabindex')).toBe('0');
        expect(element.getAttribute('aria-label') ?? '').not.toBe('');
      }
      for (const element of Array.from(screen().querySelectorAll('[click], [ng-reflect-click]'))) {
        expect(['BUTTON', 'A']).toContain(element.tagName);
      }
    });

    it('has one main landmark, one h1 and a labelled h2 for each area in stacking order', () => {
      expect(screen().querySelectorAll('main')).toHaveLength(1);
      expect(textsOf('h1')).toEqual([employeeCopy.page.title]);
      expect(textsOf('h2')).toEqual([
        employeeCopy.chat.heading,
        employeeCopy.details.heading,
        employeeCopy.comparison.heading,
      ]);
      for (const section of screen().querySelectorAll('section')) {
        const heading = screen().querySelector(`#${section.getAttribute('aria-labelledby')}`);
        expect(heading?.tagName).toBe('H2');
      }
    });

    it('shows the empty comparison message until the conversation has a comparison', async () => {
      const area = (): Element | null => screen().querySelector('.comparison');
      expect(area()?.textContent).toContain(comparisonCopy.empty);
      expect(area()?.querySelector('table')).toBeNull();

      store().state.set(
        conversationState('c1', {
          comparison: { today: todayView(), alternatives: [alternativeView(0)] },
        }),
      );

      await until(() => expect(area()?.querySelector('table')).not.toBeNull());
      expect(area()?.textContent).not.toContain(comparisonCopy.empty);
    });

    it('opens a new conversation from the header, and the earlier one still opens', async () => {
      api.createConversation.mockResolvedValue(conversationState('c2'));

      click(NEW_CONVERSATION);
      await until(() => expect(router.url).toBe('/employee/c2'));
      await until(() => expect(store().state()?.id).toBe('c2'));

      await harness.navigateByUrl('/employee/c1');
      await until(() => expect(store().state()?.id).toBe('c1'));
    });

    it('creates only one conversation for repeated clicks', async () => {
      const pending = deferred<ConversationState>();
      api.createConversation.mockReturnValue(pending.promise);

      click(NEW_CONVERSATION);
      click(NEW_CONVERSATION);
      pending.resolve(conversationState('c2'));

      await until(() => expect(router.url).toBe('/employee/c2'));
      expect(api.createConversation).toHaveBeenCalledOnce();
    });

    it('announces a notice and stays on the conversation when none can be created', async () => {
      api.createConversation.mockRejectedValue(new ApiError(0, 'network_error'));

      click(NEW_CONVERSATION);

      await until(() =>
        expect(screen().querySelector('[aria-live="assertive"]')?.textContent).toContain(
          employeeCopy.notices.conversation_not_created,
        ),
      );
      expect(router.url).toBe('/employee/c1');
    });
  });
});
