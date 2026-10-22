import { computed, DestroyRef, inject, Injectable, signal } from '@angular/core';

import { ApiClient, ApiError } from '../shared/api';
import {
  CarKey,
  ConversationState,
  DocumentCandidates,
  FieldError,
  ProfileChanges,
} from '../shared/api-models';
import { POLL_INTERVAL_MS, pollWhile } from '../shared/poller';
import { NoticeKey } from './employee.copy';

export interface WriteResult {
  readonly ok: boolean;
  // The fields that the server named, so the panel can show each message next to its input.
  readonly fields: readonly FieldError[];
}

// How long the screen goes on checking after the last open question was answered. The answer is
// stored first and the resumption takes the run guard a moment later, and a check in between
// would find no open question and no run (Req 10.12).
const RESUME_WAIT_MS = 15_000;

@Injectable()
export class EmployeeStore {
  private readonly api = inject(ApiClient);
  // A response only counts while the conversation it was sent for is still the one on screen.
  private generation = 0;
  private requestsInFlight = 0;
  // Counts the starts and the ends of writes. A reload that overlaps one may hold the state from
  // before it, so its answer is dropped and the next check brings the state.
  private writes = 0;
  private resumeTimer: ReturnType<typeof setTimeout> | undefined;

  readonly state = signal<ConversationState | null>(null);
  readonly sending = signal(false);
  readonly notice = signal<NoticeKey | null>(null);
  // Set while a check for updates fails, until the next one works. The last state stays shown.
  readonly updateFailed = signal(false);
  // Document upload state (Req 13).
  readonly uploading = signal(false);
  readonly documentCandidates = signal<DocumentCandidates | null>(null);
  readonly documentError = signal<string | null>(null);

  private readonly resuming = signal(false);

  // The screen checks for updates only while a question waits for Transport_Rep or the agent
  // runs, including the resumption after an answer (Req 10.12, 10.13).
  readonly needsPolling = computed(() => {
    const state = this.state();
    return (
      state !== null && (state.open_questions.length > 0 || state.agent.running || this.resuming())
    );
  });

  constructor() {
    pollWhile(this.needsPolling, POLL_INTERVAL_MS, () => this.refresh());
    inject(DestroyRef).onDestroy(() => clearTimeout(this.resumeTimer));
  }

  show(state: ConversationState): void {
    this.generation += 1;
    this.requestsInFlight = 0;
    this.sending.set(false);
    this.notice.set(null);
    this.updateFailed.set(false);
    this.stopResuming();
    this.state.set(state);
  }

  // Reads the stored conversation again, for the checks of the polling and after a refusal.
  async refresh(): Promise<void> {
    const conversationId = this.state()?.id;
    if (conversationId === undefined) {
      return;
    }
    const generation = this.generation;
    const writes = this.writes;
    try {
      const next = await this.api.getConversation(conversationId);
      if (generation === this.generation) {
        this.updateFailed.set(false);
        if (writes === this.writes) {
          this.follow(this.state(), next);
          this.state.set(next);
        }
      }
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        throw failure;
      }
      if (generation === this.generation) {
        this.updateFailed.set(true);
      }
    }
  }

  async send(text: string): Promise<WriteResult> {
    const result = await this.apply(
      (conversationId) => this.api.sendMessage(conversationId, text),
      (error) => (error.code === 'agent_busy' ? 'agent_busy' : 'message_not_sent'),
    );
    if (!result.ok && this.notice() === 'agent_busy') {
      // The screen did not know that a run was going on. Reading it starts the checks.
      void this.refresh();
    }
    return result;
  }

  saveField(changes: ProfileChanges): Promise<WriteResult> {
    return this.apply(
      (conversationId) => this.api.patchProfile(conversationId, changes),
      // The panel shows a rejected field next to its input, so only a failure that names no field
      // raises a notice.
      (error) => (error.fields.length > 0 ? null : 'change_not_saved'),
    );
  }

  async upload(file: File): Promise<void> {
    const conversationId = this.state()?.id;
    if (conversationId === undefined) {
      return;
    }
    this.uploading.set(true);
    this.documentError.set(null);
    this.documentCandidates.set(null);
    try {
      const candidates = await this.api.uploadDocument(conversationId, file);
      this.documentCandidates.set(candidates);
    } catch (failure) {
      if (failure instanceof ApiError) {
        const errorMap: Record<string, string> = {
          invalid_file: 'invalidFile',
          nothing_detected: 'nothingDetected',
          reader_failed: 'readerFailed',
          reader_timeout: 'readerTimeout',
        };
        this.documentError.set(errorMap[failure.code] ?? 'readerFailed');
      } else {
        this.documentError.set('readerFailed');
      }
    } finally {
      this.uploading.set(false);
    }
  }

  async confirmDocument(changes: ProfileChanges): Promise<WriteResult> {
    const result = await this.apply(
      (conversationId) => this.api.confirmDocument(conversationId, changes),
      () => 'change_not_saved',
    );
    if (result.ok) {
      // The confirm endpoint triggers a background agent run. Start the resume timer so polling
      // picks up the agent's reply even though the returned state may not show agent.running yet.
      clearTimeout(this.resumeTimer);
      this.resuming.set(true);
      this.resumeTimer = setTimeout(() => this.resuming.set(false), RESUME_WAIT_MS);
    }
    return result;
  }

  cancelDocument(): void {
    this.documentCandidates.set(null);
    this.documentError.set(null);
  }

  replaceCar(position: number, carKey: CarKey): Promise<WriteResult> {
    return this.apply(
      (conversationId) => this.api.replaceAlternative(conversationId, position, carKey),
      () => 'change_not_saved',
    );
  }

  private async apply(
    request: (conversationId: string) => Promise<ConversationState>,
    noticeFor: (error: ApiError) => NoticeKey | null,
  ): Promise<WriteResult> {
    const conversationId = this.state()?.id;
    if (conversationId === undefined) {
      throw new Error('No conversation is open.');
    }
    const generation = this.generation;
    this.notice.set(null);
    this.writes += 1;
    this.requestsInFlight += 1;
    this.sending.set(true);
    try {
      const next = await request(conversationId);
      if (generation === this.generation) {
        this.state.set(next);
      }
      return { ok: true, fields: [] };
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        throw failure;
      }
      if (generation === this.generation) {
        this.notice.set(noticeFor(failure));
      }
      return { ok: false, fields: failure.fields };
    } finally {
      this.writes += 1;
      if (generation === this.generation) {
        this.requestsInFlight -= 1;
        this.sending.set(this.requestsInFlight > 0);
      }
    }
  }

  // After the last open question was answered, the checks go on until the message of the
  // resumption shows, or for RESUME_WAIT_MS if it never does.
  private follow(previous: ConversationState | null, next: ConversationState): void {
    const newest = (state: ConversationState | null): number => state?.messages.at(-1)?.id ?? 0;
    if (newest(next) > newest(previous)) {
      this.stopResuming();
    } else if (
      previous !== null &&
      previous.open_questions.length > 0 &&
      next.open_questions.length === 0
    ) {
      clearTimeout(this.resumeTimer);
      this.resuming.set(true);
      this.resumeTimer = setTimeout(() => this.resuming.set(false), RESUME_WAIT_MS);
    }
  }

  private stopResuming(): void {
    clearTimeout(this.resumeTimer);
    this.resuming.set(false);
  }
}
