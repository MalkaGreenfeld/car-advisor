import { TestBed } from '@angular/core/testing';

import { ApiClient, ApiError } from '../shared/api';
import { ConversationState, OpenQuestion, StoredMessage } from '../shared/api-models';
import { POLL_INTERVAL_MS } from '../shared/poller';
import { EmployeeStore } from './employee-store';
import { conversationState } from './testing/conversation-state';
import { deferred } from './testing/deferred';

const CAR_KEY = '2026-2026-1-01229-00004';

const reply: StoredMessage = {
  id: 2,
  role: 'advisor',
  text: 'מה השכר הבסיסי שלך?',
  notice: null,
  created_at: '2026-10-13T09:12:03Z',
};

function createFakeApi() {
  return {
    getConversation: vi.fn<ApiClient['getConversation']>(),
    sendMessage: vi.fn<ApiClient['sendMessage']>(),
    patchProfile: vi.fn<ApiClient['patchProfile']>(),
    replaceAlternative: vi.fn<ApiClient['replaceAlternative']>(),
  };
}

describe('EmployeeStore', () => {
  let api: ReturnType<typeof createFakeApi>;
  let store: EmployeeStore;

  beforeEach(() => {
    api = createFakeApi();
    TestBed.configureTestingModule({
      providers: [EmployeeStore, { provide: ApiClient, useValue: api }],
    });
    store = TestBed.inject(EmployeeStore);
  });

  it('rejects a request while no conversation is open', async () => {
    await expect(store.send('שלום')).rejects.toThrow('No conversation is open.');
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  describe('with a conversation open', () => {
    beforeEach(() => {
      store.show(conversationState('c1'));
    });

    describe('send', () => {
      it('sends the text and shows the returned state', async () => {
        const next = conversationState('c1', { messages: [reply] });
        api.sendMessage.mockResolvedValue(next);

        const result = await store.send('שלום');

        expect(api.sendMessage).toHaveBeenCalledWith('c1', 'שלום');
        expect(result).toEqual({ ok: true, fields: [] });
        expect(store.state()).toBe(next);
        expect(store.notice()).toBeNull();
      });

      it.each([
        { failure: new ApiError(409, 'agent_busy'), notice: 'agent_busy' },
        { failure: new ApiError(0, 'network_error'), notice: 'message_not_sent' },
        { failure: new ApiError(500, 'unexpected_response'), notice: 'message_not_sent' },
      ])('raises $notice when the request fails with $failure.code', async (scenario) => {
        const before = store.state();
        api.sendMessage.mockRejectedValue(scenario.failure);
        // A refusal because the agent is busy makes the store read the conversation again.
        api.getConversation.mockResolvedValue(before as ConversationState);

        const result = await store.send('שלום');

        expect(result).toEqual({ ok: false, fields: [] });
        expect(store.notice()).toBe(scenario.notice);
        expect(store.state()).toBe(before);
      });
    });

    describe('saveField', () => {
      it('patches the profile and shows the returned state', async () => {
        const next = conversationState('c1');
        api.patchProfile.mockResolvedValue(next);

        const result = await store.saveField({ base_salary: '16000' });

        expect(api.patchProfile).toHaveBeenCalledWith('c1', { base_salary: '16000' });
        expect(result.ok).toBe(true);
        expect(store.state()).toBe(next);
      });

      it('returns the fields that the server rejected, without a notice', async () => {
        const fields = [{ field: 'base_salary', message: 'הסכום חייב להיות לפחות 0' }];
        api.patchProfile.mockRejectedValue(new ApiError(422, 'invalid_fields', fields));

        const result = await store.saveField({ base_salary: '-1' });

        expect(result).toEqual({ ok: false, fields });
        expect(store.notice()).toBeNull();
      });

      it('raises "not saved" when the failure names no field', async () => {
        api.patchProfile.mockRejectedValue(new ApiError(0, 'network_error'));

        const result = await store.saveField({ base_salary: '16000' });

        expect(result).toEqual({ ok: false, fields: [] });
        expect(store.notice()).toBe('change_not_saved');
      });
    });

    describe('replaceCar', () => {
      it('replaces the car in the given column and shows the returned state', async () => {
        const next = conversationState('c1', { alternatives: [{ kind: 'car', car_key: CAR_KEY }] });
        api.replaceAlternative.mockResolvedValue(next);

        const result = await store.replaceCar(1, CAR_KEY);

        expect(api.replaceAlternative).toHaveBeenCalledWith('c1', 1, CAR_KEY);
        expect(result.ok).toBe(true);
        expect(store.state()).toBe(next);
      });

      it('keeps the state and raises "not saved" when the request fails', async () => {
        const before = store.state();
        api.replaceAlternative.mockRejectedValue(new ApiError(503, 'database_unavailable'));

        const result = await store.replaceCar(1, CAR_KEY);

        expect(result.ok).toBe(false);
        expect(store.state()).toBe(before);
        expect(store.notice()).toBe('change_not_saved');
      });
    });

    it('clears the notice when the next request starts', async () => {
      api.replaceAlternative.mockRejectedValue(new ApiError(0, 'network_error'));
      await store.replaceCar(1, CAR_KEY);
      expect(store.notice()).toBe('change_not_saved');

      const pending = deferred<ConversationState>();
      api.sendMessage.mockReturnValue(pending.promise);
      const sent = store.send('שלום');
      expect(store.notice()).toBeNull();

      pending.resolve(conversationState('c1'));
      await sent;
    });

    it('is sending from the start of a request until it settles', async () => {
      const pending = deferred<ConversationState>();
      api.sendMessage.mockReturnValue(pending.promise);

      const sent = store.send('שלום');
      expect(store.sending()).toBe(true);

      pending.resolve(conversationState('c1'));
      await sent;
      expect(store.sending()).toBe(false);
    });

    it('keeps sending until the last of two overlapping requests settles', async () => {
      const slow = deferred<ConversationState>();
      const fast = deferred<ConversationState>();
      api.sendMessage.mockReturnValue(slow.promise);
      api.patchProfile.mockReturnValue(fast.promise);

      const sent = store.send('שלום');
      const saved = store.saveField({ display_name: 'דנה' });
      fast.resolve(conversationState('c1'));
      await saved;
      expect(store.sending()).toBe(true);

      slow.resolve(conversationState('c1'));
      await sent;
      expect(store.sending()).toBe(false);
    });

    describe('when another conversation is shown before the response arrives', () => {
      it('ignores a response', async () => {
        const pending = deferred<ConversationState>();
        api.sendMessage.mockReturnValue(pending.promise);
        const sent = store.send('שלום');
        const other = conversationState('c2');
        store.show(other);

        pending.resolve(conversationState('c1', { messages: [reply] }));
        await sent;

        expect(store.state()).toBe(other);
        expect(store.sending()).toBe(false);
      });

      it('ignores a failure', async () => {
        const pending = deferred<ConversationState>();
        api.sendMessage.mockReturnValue(pending.promise);
        const sent = store.send('שלום');
        store.show(conversationState('c2'));

        pending.reject(new ApiError(0, 'network_error'));
        await sent;

        expect(store.notice()).toBeNull();
      });
    });
  });

  describe('polling', () => {
    const question: OpenQuestion = {
      field: 'new_car_participation',
      car_display_name: 'Kia EV6',
      car_key: CAR_KEY,
    };
    const waiting = () => conversationState('c1', { open_questions: [question] });
    const running = () => conversationState('c1', { agent: { available: true, running: true } });
    const answered = (messages: StoredMessage[] = [reply]) => conversationState('c1', { messages });

    beforeEach(() => {
      vi.useFakeTimers();
      api.getConversation.mockResolvedValue(conversationState('c1'));
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    // Shows the state and lets the polling effect see it.
    function show(state: ConversationState): void {
      store.show(state);
      TestBed.tick();
    }

    async function elapse(ms: number): Promise<void> {
      await vi.advanceTimersByTimeAsync(ms);
      TestBed.tick();
    }

    it('does not check while no question is open and no run is going on', async () => {
      show(conversationState('c1'));

      await elapse(POLL_INTERVAL_MS * 3);

      expect(api.getConversation).not.toHaveBeenCalled();
      expect(store.needsPolling()).toBe(false);
    });

    it('does not check before a conversation is shown', async () => {
      await elapse(POLL_INTERVAL_MS * 3);

      expect(api.getConversation).not.toHaveBeenCalled();
    });

    it('checks every 3 seconds while a question is open', async () => {
      api.getConversation.mockResolvedValue(waiting());
      show(waiting());

      await elapse(POLL_INTERVAL_MS - 1);
      expect(api.getConversation).not.toHaveBeenCalled();
      await elapse(1);
      expect(api.getConversation).toHaveBeenCalledExactlyOnceWith('c1');
      await elapse(POLL_INTERVAL_MS * 2);
      expect(api.getConversation).toHaveBeenCalledTimes(3);
    });

    it('checks while the agent runs, and stops when the run is over', async () => {
      api.getConversation.mockResolvedValueOnce(running());
      api.getConversation.mockResolvedValueOnce(answered());
      show(running());

      await elapse(POLL_INTERVAL_MS * 2);
      expect(store.state()?.messages).toEqual([reply]);
      expect(api.getConversation).toHaveBeenCalledTimes(2);

      await elapse(POLL_INTERVAL_MS * 4);
      expect(api.getConversation).toHaveBeenCalledTimes(2);
    });

    it('stops once the question is answered and the message of the resumption shows', async () => {
      api.getConversation.mockResolvedValueOnce(answered());
      show(waiting());

      await elapse(POLL_INTERVAL_MS);
      expect(store.state()?.open_questions).toEqual([]);
      expect(store.needsPolling()).toBe(false);

      await elapse(POLL_INTERVAL_MS * 4);
      expect(api.getConversation).toHaveBeenCalledTimes(1);
    });

    it('goes on after the answer until the resumption has stored its message', async () => {
      api.getConversation.mockResolvedValueOnce(conversationState('c1'));
      api.getConversation.mockResolvedValueOnce(conversationState('c1'));
      api.getConversation.mockResolvedValueOnce(answered());
      show(waiting());

      await elapse(POLL_INTERVAL_MS);
      expect(store.needsPolling()).toBe(true);
      await elapse(POLL_INTERVAL_MS);
      expect(store.needsPolling()).toBe(true);
      await elapse(POLL_INTERVAL_MS);

      expect(store.state()?.messages).toEqual([reply]);
      expect(store.needsPolling()).toBe(false);
      await elapse(POLL_INTERVAL_MS * 4);
      expect(api.getConversation).toHaveBeenCalledTimes(3);
    });

    it('gives up waiting for the resumption after a while', async () => {
      api.getConversation.mockResolvedValue(conversationState('c1'));
      show(waiting());

      await elapse(POLL_INTERVAL_MS);
      expect(store.needsPolling()).toBe(true);
      await elapse(20_000);

      expect(store.needsPolling()).toBe(false);
      const calls = api.getConversation.mock.calls.length;
      await elapse(POLL_INTERVAL_MS * 4);
      expect(api.getConversation).toHaveBeenCalledTimes(calls);
    });

    describe('a check that fails', () => {
      it('keeps the state, says so, and clears the message at the next check that works', async () => {
        const shown = waiting();
        api.getConversation.mockRejectedValueOnce(new ApiError(0, 'network_error'));
        api.getConversation.mockResolvedValue(waiting());
        show(shown);

        await elapse(POLL_INTERVAL_MS);
        expect(store.state()).toBe(shown);
        expect(store.updateFailed()).toBe(true);
        expect(store.needsPolling()).toBe(true);

        await elapse(POLL_INTERVAL_MS);
        expect(store.updateFailed()).toBe(false);
      });

      it('does not touch the notice of a write', async () => {
        api.getConversation.mockRejectedValue(new ApiError(503, 'database_unavailable'));
        show(waiting());

        await elapse(POLL_INTERVAL_MS);

        expect(store.notice()).toBeNull();
      });
    });

    describe('a check that overlaps something else', () => {
      it('is dropped when a write started or ended meanwhile, and the next check brings the state', async () => {
        const slowRead = deferred<ConversationState>();
        api.getConversation.mockReturnValueOnce(slowRead.promise);
        const written = conversationState('c1', {
          open_questions: [question],
          messages: [reply],
        });
        api.patchProfile.mockResolvedValue(written);
        show(waiting());
        await elapse(POLL_INTERVAL_MS);

        await store.saveField({ display_name: 'דנה' });
        slowRead.resolve(waiting());
        await elapse(0);

        expect(store.state()).toBe(written);
      });

      it('is ignored when another conversation is shown meanwhile', async () => {
        const slowRead = deferred<ConversationState>();
        api.getConversation.mockReturnValueOnce(slowRead.promise);
        show(waiting());
        await elapse(POLL_INTERVAL_MS);
        const other = conversationState('c2');

        show(other);
        slowRead.resolve(waiting());
        await elapse(0);

        expect(store.state()).toBe(other);
      });

      it('is ignored, with its failure, when another conversation is shown meanwhile', async () => {
        const slowRead = deferred<ConversationState>();
        api.getConversation.mockReturnValueOnce(slowRead.promise);
        show(waiting());
        await elapse(POLL_INTERVAL_MS);

        show(conversationState('c2'));
        slowRead.reject(new ApiError(0, 'network_error'));
        await elapse(0);

        expect(store.updateFailed()).toBe(false);
      });
    });

    describe('a message that is refused because the agent is busy', () => {
      it('reads the conversation, so the screen learns that a run is going on and starts to check', async () => {
        show(conversationState('c1'));
        api.sendMessage.mockRejectedValue(new ApiError(409, 'agent_busy'));
        api.getConversation.mockResolvedValue(running());

        const result = await store.send('שלום');
        await elapse(0);

        expect(result.ok).toBe(false);
        expect(store.notice()).toBe('agent_busy');
        expect(api.getConversation).toHaveBeenCalledExactlyOnceWith('c1');
        expect(store.state()?.agent.running).toBe(true);
        expect(store.needsPolling()).toBe(true);
      });

      it('does not read the conversation for another failure', async () => {
        show(conversationState('c1'));
        api.sendMessage.mockRejectedValue(new ApiError(0, 'network_error'));

        await store.send('שלום');
        await elapse(0);

        expect(api.getConversation).not.toHaveBeenCalled();
      });
    });
  });
});
