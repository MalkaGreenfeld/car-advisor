import { Component, computed, DestroyRef, effect, inject, input, signal, untracked } from '@angular/core';
import { Router } from '@angular/router';

import { ApiClient, ApiError } from '../shared/api';
import { sharedCopy } from '../shared/shared.copy';
import { Chat } from './chat/chat';
import { ComparisonSummary } from './comparison/comparison-summary';
import { EmployeeStore } from './employee-store';
import { employeeCopy } from './employee.copy';
import { MyDetails } from './my-details/my-details';

type LoadStatus = 'loading' | 'ready' | 'not_found' | 'failed';

// The fields from the profile that count toward "filled".
const TRACKED_FIELDS = [
  'occupation',
  'base_salary',
  'pension_rate_percent',
  'study_fund_rate_percent',
  'credit_points',
  'has_current_car',
  'children_under_23',
  'display_name',
  'pay_components',
] as const;

@Component({
  selector: 'app-employee-page',
  imports: [Chat, ComparisonSummary, MyDetails],
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
  private generation = 0;
  private creating = false;

  // Progress indicator: how many of the tracked fields have a value.
  private readonly filledCount = computed(() => {
    const profile = this.store.state()?.profile;
    if (!profile) {
      return 0;
    }
    let count = 0;
    for (const field of TRACKED_FIELDS) {
      const value = profile[field];
      if (value !== null && value !== undefined) {
        count++;
      }
    }
    return count;
  });

  protected readonly detailsProgress = computed(() => {
    const filled = this.filledCount();
    const total = TRACKED_FIELDS.length;
    return `${filled} מתוך ${total}`;
  });

  protected readonly detailsPercent = computed(() => {
    return Math.round((this.filledCount() / TRACKED_FIELDS.length) * 100);
  });

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
