import { Component, DestroyRef, effect, inject, input, signal, untracked } from '@angular/core';
import { Router } from '@angular/router';

import { ApiClient, ApiError } from '../shared/api';
import { sharedCopy } from '../shared/shared.copy';
import { Chat } from './chat/chat';
import { ComparisonTable } from './comparison/comparison-table';
import { EmployeeStore } from './employee-store';
import { employeeCopy } from './employee.copy';
import { MyDetails } from './my-details/my-details';

type LoadStatus = 'loading' | 'ready' | 'not_found' | 'failed';

@Component({
  selector: 'app-employee-page',
  imports: [Chat, ComparisonTable, MyDetails],
  providers: [EmployeeStore],
  templateUrl: './employee-page.html',
  styleUrl: './employee-page.scss',
})
export class EmployeePage {
  private readonly api = inject(ApiClient);
  private readonly router = inject(Router);
  protected readonly store = inject(EmployeeStore);
  protected readonly copy = employeeCopy;
  protected readonly sharedCopy = sharedCopy;

  readonly conversationId = input<string>();

  protected readonly status = signal<LoadStatus>('loading');
  // The router reuses this page when only the ID changes, and a late response of an earlier ID, or
  // of a page that is gone, must not change what the screen shows.
  private generation = 0;
  // Repeated clicks are ignored rather than disabling the button, which would drop its focus.
  private creating = false;

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.generation += 1;
    });
    effect(() => {
      const conversationId = this.conversationId();
      untracked(() => void this.open(conversationId));
    });
  }

  protected retry(): Promise<void> {
    return this.open(this.conversationId());
  }

  protected async startNew(): Promise<void> {
    if (this.creating) {
      return;
    }
    const generation = this.generation;
    try {
      const createdId = await this.createConversation();
      if (generation === this.generation) {
        await this.router.navigate(['/employee', createdId]);
      }
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        throw failure;
      }
      if (generation === this.generation) {
        this.store.notice.set('conversation_not_created');
      }
    }
  }

  private async open(conversationId: string | undefined): Promise<void> {
    this.generation += 1;
    const generation = this.generation;
    this.status.set('loading');
    try {
      if (conversationId === undefined) {
        const createdId = await this.createConversation();
        if (generation === this.generation) {
          await this.router.navigate(['/employee', createdId], { replaceUrl: true });
        }
      } else {
        const state = await this.api.getConversation(conversationId);
        if (generation === this.generation) {
          this.store.show(state);
          this.status.set('ready');
        }
      }
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        throw failure;
      }
      if (generation === this.generation) {
        // A 404 is a conversation that does not exist, also when the path is one that no route matches,
        // such as an ID with an encoded slash (Req 11.11).
        const missing = failure.status === 404 || failure.code === 'conversation_not_found';
        this.status.set(missing ? 'not_found' : 'failed');
      }
    }
  }

  private async createConversation(): Promise<string> {
    this.creating = true;
    try {
      const created = await this.api.createConversation();
      return created.id;
    } finally {
      this.creating = false;
    }
  }
}
