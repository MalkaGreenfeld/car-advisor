import { Component, computed, DestroyRef, inject, Injector, signal } from '@angular/core';

import { ApiClient, ApiError } from '../shared/api';
import { RepQuestionStatus, RepQuestionView } from '../shared/api-models';
import { POLL_INTERVAL_MS, pollWhile } from '../shared/poller';
import { sharedCopy } from '../shared/shared.copy';
import { AnswerFormState, answerKindOf, createAnswerForm, initialValuesOf } from './answer-values';
import { RepQuestion } from './rep-question';
import { repCopy } from './rep.copy';

type LoadStatus = 'loading' | 'ready' | 'failed';

// How long the confirmation that an answer was saved stays on the screen.
const CONFIRMATION_MS = 8000;

const STATUS_RANK: Readonly<Record<RepQuestionStatus, number>> = { open: 0, answered: 1 };

// The server sorts the same way, but the screen does not depend on it. The times are compared as
// times: the server leaves the fraction out when it is zero, so the texts do not sort as times.
function inDisplayOrder(questions: readonly RepQuestionView[]): RepQuestionView[] {
  return [...questions].sort(
    (a, b) =>
      STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
      Date.parse(a.created_at) - Date.parse(b.created_at),
  );
}

@Component({
  selector: 'app-rep-page',
  imports: [RepQuestion],
  templateUrl: './rep-page.html',
  styleUrl: './rep-page.scss',
})
export class RepPage {
  private readonly api = inject(ApiClient);
  private readonly injector = inject(Injector);
  protected readonly copy = repCopy;
  protected readonly sharedCopy = sharedCopy;

  protected readonly status = signal<LoadStatus>('loading');
  protected readonly questions = signal<readonly RepQuestionView[]>([]);
  protected readonly updateFailed = signal(false);
  protected readonly confirmation = signal<string | null>(null);

  protected readonly openQuestions = computed(() =>
    this.questions().filter((question) => question.status === 'open'),
  );
  protected readonly answeredQuestions = computed(() =>
    this.questions().filter((question) => question.status === 'answered'),
  );

  // The form of every open question, kept here so that a reload of the list never resets what was
  // typed, even when the question is not in a list for a moment.
  private readonly forms = new Map<string, AnswerFormState>();
  private confirmationTimer: ReturnType<typeof setTimeout> | undefined;
  private started = 0;
  private newestShown = 0;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.confirmationTimer));
    pollWhile(signal(true), POLL_INTERVAL_MS, () => this.reload());
    void this.reload();
  }

  protected retry(): Promise<void> {
    this.status.set('loading');
    return this.reload();
  }

  protected formOf(questionId: string): AnswerFormState | undefined {
    return this.forms.get(questionId);
  }

  protected onAnswered(answered: RepQuestionView): void {
    this.forms.delete(answered.id);
    this.questions.update((questions) =>
      inDisplayOrder(
        questions.map((question) => (question.id === answered.id ? answered : question)),
      ),
    );
    clearTimeout(this.confirmationTimer);
    this.confirmation.set(repCopy.answer.saved);
    this.confirmationTimer = setTimeout(() => this.confirmation.set(null), CONFIRMATION_MS);
  }

  // Someone else answered first. The reload brings the stored answer, which is then shown read-only.
  protected onAlreadyAnswered(): void {
    void this.reload();
  }

  protected async reload(): Promise<void> {
    this.started += 1;
    const request = this.started;
    try {
      const response = await this.api.listRepQuestions();
      if (this.isCurrent(request)) {
        this.ensureForms(response.questions);
        this.questions.set(inDisplayOrder(response.questions));
        this.updateFailed.set(false);
        this.status.set('ready');
      }
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        throw failure;
      }
      if (this.isCurrent(request)) {
        // Without a first list there is nothing to keep on screen.
        if (this.status() === 'ready') {
          this.updateFailed.set(true);
        } else {
          this.status.set('failed');
        }
      }
    }
  }

  // Forms are made here, in a callback, and not in the template: creating one inside a template
  // would run in a reactive context.
  private ensureForms(questions: readonly RepQuestionView[]): void {
    for (const question of questions) {
      if (question.status === 'open' && !this.forms.has(question.id)) {
        this.forms.set(
          question.id,
          createAnswerForm(answerKindOf(question), this.injector, initialValuesOf(question)),
        );
      }
    }
  }

  // An outcome counts unless a newer request has already shown its own. Dropping it merely because
  // a newer request has started would starve a server that is slower than the poll interval, as
  // every reply would arrive after the next request began.
  private isCurrent(request: number): boolean {
    if (request < this.newestShown) {
      return false;
    }
    this.newestShown = request;
    return true;
  }
}
