import { Component, computed, inject, linkedSignal, signal } from '@angular/core';

import { StoredMessage } from '../../shared/api-models';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { Composer } from './composer';
import { ChatMessage, MessageList } from './message-list';
import { TypingIndicator } from './typing-indicator';

interface Thread {
  readonly conversationId: string | undefined;
  readonly messages: readonly ChatMessage[];
}

// What the two live regions hold. A message is new when its ID is above the last one that was in
// the conversation before, so a refresh without a new message changes nothing in the regions.
interface Announced {
  readonly conversationId: string | undefined;
  readonly lastId: number;
  readonly advisor: readonly ChatMessage[];
  readonly notices: readonly ChatMessage[];
}

function toChatMessage(message: StoredMessage): ChatMessage {
  const text = message.notice === null ? message.text : employeeCopy.notices[message.notice];
  return { id: message.id, role: message.role, text: text ?? '' };
}

function announce(thread: Thread, previous: Announced | undefined): Announced {
  const lastId = thread.messages.at(-1)?.id ?? 0;
  if (previous === undefined || previous.conversationId !== thread.conversationId) {
    // What is on screen when a conversation opens is not news.
    return { conversationId: thread.conversationId, lastId, advisor: [], notices: [] };
  }
  const arrived = thread.messages.filter((message) => message.id > previous.lastId);
  if (arrived.length === 0) {
    return previous;
  }
  return {
    conversationId: thread.conversationId,
    lastId,
    advisor: arrived.filter((message) => message.role === 'advisor'),
    notices: arrived.filter((message) => message.role === 'notice'),
  };
}

@Component({
  selector: 'app-chat',
  imports: [Composer, MessageList, TypingIndicator],
  templateUrl: './chat.html',
  styleUrl: './chat.scss',
})
export class Chat {
  private readonly store = inject(EmployeeStore);

  protected readonly draft = signal('');
  // True from the moment a message is sent until the server has answered. The indicator follows
  // this flag, not store.sending(), which is also true while the details panel saves a field.
  protected readonly waiting = signal(false);

  private readonly running = computed(() => this.store.state()?.agent.running ?? false);
  protected readonly typing = computed(() => this.waiting() || this.running());
  protected readonly sendDisabled = computed(() => this.store.sending() || this.running());

  private readonly thread = computed<Thread>(() => {
    const state = this.store.state();
    return { conversationId: state?.id, messages: (state?.messages ?? []).map(toChatMessage) };
  });
  protected readonly messages = computed(() => this.thread().messages);
  protected readonly announced = linkedSignal<Thread, Announced>({
    source: this.thread,
    computation: (thread, previous) => announce(thread, previous?.value),
  });

  private agentFailed(): boolean {
    return this.store.state()?.messages.at(-1)?.notice === 'agent_failed';
  }

  protected async send(text: string): Promise<void> {
    // The button follows this state only after the next render, so a second Enter can come first.
    if (this.sendDisabled()) {
      return;
    }
    this.waiting.set(true);
    try {
      const result = await this.store.send(text);
      // The message is stored, and the notice says that it can be sent again, so the text stays.
      if (result.ok && !this.agentFailed()) {
        this.draft.set('');
      }
    } finally {
      this.waiting.set(false);
    }
  }
}
