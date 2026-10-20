import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../../shared/api';
import { ConversationState, MessageRole, NoticeCode, StoredMessage } from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { conversationState } from '../testing/conversation-state';
import { deferred } from '../testing/deferred';
import { Chat } from './chat';

const REPLY = 'ההפרש החודשי הוא +₪210.';

function message(
  id: number,
  role: MessageRole,
  text: string | null,
  notice: NoticeCode | null = null,
): StoredMessage {
  return { id, role, text, notice, created_at: '2026-10-13T09:12:03Z' };
}

// A screen reader reads out the nodes that are added to a live region.
function watchAdditions(region: Element): () => string[] {
  const added: string[] = [];
  const collect = (records: MutationRecord[]): void => {
    for (const record of records) {
      record.addedNodes.forEach((node) => added.push(node.textContent ?? ''));
    }
  };
  const observer = new MutationObserver(collect);
  observer.observe(region, { childList: true, subtree: true });
  return () => {
    collect(observer.takeRecords());
    observer.disconnect();
    return added;
  };
}

function createFakeApi() {
  return {
    sendMessage: vi.fn<ApiClient['sendMessage']>(),
    patchProfile: vi.fn<ApiClient['patchProfile']>(),
  };
}

describe('Chat', () => {
  let api: ReturnType<typeof createFakeApi>;
  let store: EmployeeStore;
  let fixture: ComponentFixture<Chat>;

  beforeEach(() => {
    api = createFakeApi();
    TestBed.configureTestingModule({
      providers: [EmployeeStore, { provide: ApiClient, useValue: api }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  async function open(state: ConversationState = conversationState('c1')): Promise<void> {
    store.show(state);
    fixture = TestBed.createComponent(Chat);
    await fixture.whenStable();
  }

  function find<T extends Element>(selector: string): T {
    const element = (fixture.nativeElement as HTMLElement).querySelector<T>(selector);
    if (element === null) {
      throw new Error(`Nothing matches ${selector}`);
    }
    return element;
  }

  function textOf(selector: string): string {
    return find(selector).textContent?.trim() ?? '';
  }

  async function type(text: string): Promise<void> {
    const field = find<HTMLTextAreaElement>('textarea');
    field.value = text;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    await fixture.whenStable();
  }

  function pressEnter(init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
      ...init,
    });
    find('textarea').dispatchEvent(event);
    return event;
  }

  // The chat works through promises that the test cannot await, so it renders and checks again
  // until the assertion holds.
  function until(assertion: () => void): Promise<void> {
    return vi.waitFor(() => {
      fixture.detectChanges();
      assertion();
    });
  }

  describe('the composer', () => {
    it('sends the trimmed text on Enter and clears the field once the reply is shown', async () => {
      api.sendMessage.mockResolvedValue(
        conversationState('c1', {
          messages: [message(1, 'employee', 'שלום'), message(2, 'advisor', REPLY)],
        }),
      );
      await open();
      await type('  שלום \n');

      const enter = pressEnter();
      await until(() => expect(find<HTMLTextAreaElement>('textarea').value).toBe(''));

      expect(enter.defaultPrevented).toBe(true);
      expect(api.sendMessage).toHaveBeenCalledOnce();
      expect(api.sendMessage).toHaveBeenCalledWith('c1', 'שלום');
    });

    it('sends the text when the button is used', async () => {
      api.sendMessage.mockResolvedValue(conversationState('c1'));
      await open();
      await type('שלום');

      find<HTMLButtonElement>('button').click();

      await until(() => expect(api.sendMessage).toHaveBeenCalledWith('c1', 'שלום'));
    });

    it('keeps the text when the agent could not answer, so that it can be sent again', async () => {
      api.sendMessage.mockResolvedValue(
        conversationState('c1', {
          messages: [message(1, 'employee', 'שלום'), message(2, 'notice', null, 'agent_failed')],
        }),
      );
      await open();
      await type('שלום');

      pressEnter();
      await until(() => expect(textOf('[aria-live="assertive"]')).not.toBe(''));

      expect(find<HTMLTextAreaElement>('textarea').value).toBe('שלום');
      expect(find<HTMLButtonElement>('button').disabled).toBe(false);
    });

    it('clears the text when the agent is unavailable, since sending it again cannot help', async () => {
      api.sendMessage.mockResolvedValue(
        conversationState('c1', {
          messages: [
            message(1, 'employee', 'שלום'),
            message(2, 'notice', null, 'agent_unavailable'),
          ],
        }),
      );
      await open();
      await type('שלום');

      pressEnter();

      await until(() => expect(find<HTMLTextAreaElement>('textarea').value).toBe(''));
    });

    it('adds a line on Shift+Enter and sends nothing', async () => {
      await open();
      await type('שורה ראשונה');

      const shiftEnter = pressEnter({ shiftKey: true });
      await fixture.whenStable();

      expect(shiftEnter.defaultPrevented).toBe(false);
      expect(api.sendMessage).not.toHaveBeenCalled();
    });

    it('sends once when Enter is pressed twice before the screen has updated', async () => {
      api.sendMessage.mockReturnValue(deferred<ConversationState>().promise);
      await open();
      await type('שלום');

      pressEnter();
      pressEnter();

      expect(api.sendMessage).toHaveBeenCalledOnce();
    });

    it('sends nothing when the message is blank', async () => {
      await open();
      await type(' \n ');

      pressEnter();
      find<HTMLButtonElement>('button').click();
      await fixture.whenStable();

      expect(api.sendMessage).not.toHaveBeenCalled();
    });

    it('keeps the text when the send fails', async () => {
      api.sendMessage.mockRejectedValue(new ApiError(0, 'network_error'));
      await open();
      await type('שלום');

      pressEnter();
      await until(() => expect(store.notice()).toBe('message_not_sent'));

      expect(find<HTMLTextAreaElement>('textarea').value).toBe('שלום');
      expect(find<HTMLButtonElement>('button').disabled).toBe(false);
    });

    it('disables the button while a run is active, and sends nothing then', async () => {
      await open(conversationState('c1', { agent: { available: true, running: true } }));
      const button = find<HTMLButtonElement>('button');
      expect(button.disabled).toBe(true);
      await type('שלום');

      pressEnter();
      await fixture.whenStable();
      expect(api.sendMessage).not.toHaveBeenCalled();

      store.state.set(conversationState('c1'));
      await fixture.whenStable();
      expect(button.disabled).toBe(false);
    });
  });

  describe('the typing indicator', () => {
    it('shows from the send until the reply, with the text locked meanwhile', async () => {
      const pending = deferred<ConversationState>();
      api.sendMessage.mockReturnValue(pending.promise);
      await open();
      await type('שלום');
      expect(textOf('app-typing-indicator')).toBe('');

      pressEnter();
      await until(() => expect(textOf('app-typing-indicator')).toContain(employeeCopy.chat.typing));
      expect(find<HTMLButtonElement>('button').disabled).toBe(true);
      expect(find<HTMLTextAreaElement>('textarea').readOnly).toBe(true);
      expect(find<HTMLTextAreaElement>('textarea').value).toBe('שלום');

      pending.resolve(
        conversationState('c1', {
          messages: [message(1, 'employee', 'שלום'), message(2, 'advisor', REPLY)],
        }),
      );
      await until(() => expect(textOf('app-typing-indicator')).toBe(''));
      expect(find<HTMLButtonElement>('button').disabled).toBe(false);
    });

    it('shows while the stored conversation says that a run is active', async () => {
      await open(conversationState('c1', { agent: { available: true, running: true } }));

      expect(textOf('app-typing-indicator')).toContain(employeeCopy.chat.typing);
    });

    it('does not show while the details panel saves a field, but the button waits', async () => {
      const pending = deferred<ConversationState>();
      api.patchProfile.mockReturnValue(pending.promise);
      await open();

      const saved = store.saveField({ base_salary: '16000' });
      await fixture.whenStable();

      expect(textOf('app-typing-indicator')).toBe('');
      expect(find<HTMLButtonElement>('button').disabled).toBe(true);
      pending.resolve(conversationState('c1'));
      await saved;
    });
  });

  describe('the messages', () => {
    it('are listed in order, with the numbers inside them isolated', async () => {
      await open(
        conversationState('c1', {
          messages: [message(1, 'employee', 'כמה אקבל?'), message(2, 'advisor', REPLY)],
        }),
      );

      const items = (fixture.nativeElement as HTMLElement).querySelectorAll('ol > li');

      expect(items).toHaveLength(2);
      expect(items[0].textContent).toContain(employeeCopy.chat.senders.employee);
      expect(items[1].textContent).toContain(REPLY);
      expect(items[1].querySelector('bdi[dir="ltr"]')?.textContent).toBe('+₪210');
    });

    it('announces a new advisor message once, and the focus stays where it was', async () => {
      const earlier = [message(1, 'employee', 'שלום'), message(2, 'advisor', 'מה השכר שלך?')];
      await open(conversationState('c1', { messages: earlier }));
      const polite = find('[aria-live="polite"]');
      const added = watchAdditions(polite);
      find<HTMLTextAreaElement>('textarea').focus();
      expect(polite.textContent?.trim()).toBe('');

      const arrived = [...earlier, message(3, 'employee', '16000'), message(4, 'advisor', REPLY)];
      store.state.set(conversationState('c1', { messages: arrived }));
      await fixture.whenStable();
      store.state.set(conversationState('c1', { messages: [...arrived] }));
      await fixture.whenStable();

      expect(added()).toEqual([REPLY]);
      expect(polite.textContent?.trim()).toBe(REPLY);
      expect(document.activeElement).toBe(find('textarea'));
    });

    it('are not announced when another conversation replaces the open one', async () => {
      await open(conversationState('c1', { messages: [message(1, 'advisor', 'שלום')] }));
      const added = watchAdditions(find('[aria-live="polite"]'));

      store.show(conversationState('c2', { messages: [message(7, 'advisor', REPLY)] }));
      await fixture.whenStable();

      expect(added()).toEqual([]);
    });

    it('show a notice from the copy by its code, and announce it in the assertive region', async () => {
      api.sendMessage.mockResolvedValue(
        conversationState('c1', {
          messages: [message(1, 'employee', 'שלום'), message(2, 'notice', null, 'agent_failed')],
        }),
      );
      await open();
      await type('שלום');

      pressEnter();
      await until(() =>
        expect(textOf('[aria-live="assertive"]')).toBe(employeeCopy.notices.agent_failed),
      );

      expect(textOf('li[data-role="notice"]')).toBe(employeeCopy.notices.agent_failed);
      expect(textOf('[aria-live="polite"]')).toBe('');
      expect(textOf('app-typing-indicator')).toBe('');
    });

    it('scroll to the newest message, and stay put when a refresh brings nothing new', async () => {
      const first = [message(1, 'employee', 'שלום')];
      await open(conversationState('c1', { messages: first }));
      const list = find<HTMLElement>('ol');
      Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 480 });

      const next = [...first, message(2, 'advisor', REPLY)];
      store.state.set(conversationState('c1', { messages: next }));
      await until(() => expect(list.scrollTop).toBe(480));

      list.scrollTop = 100;
      store.state.set(conversationState('c1', { messages: [...next] }));
      await fixture.whenStable();
      expect(list.scrollTop).toBe(100);
    });
  });
});
