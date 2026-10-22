import { Component, computed, effect, ElementRef, inject, signal, viewChild } from '@angular/core';

import { AlternativeView } from '../../shared/api-models';
import { BidiText } from '../../shared/bidi-text';
import { EmployeeStore } from '../employee-store';
import { Amount } from './amount';
import { ComparisonTable } from './comparison-table';
import { comparisonCopy } from './comparison.copy';

const copy = comparisonCopy;

type SummaryState = 'empty' | 'no_alternatives' | 'missing' | 'ready';

interface Chip {
  readonly id: string;
  readonly title: string;
  readonly car: string;
  readonly status: AlternativeView['status'];
  readonly difference: number | null;
}

function carName(view: AlternativeView): string {
  if (view.car === null) {
    return copy.car.waiver;
  }
  return view.car.display_name ?? view.car.car_key;
}

@Component({
  selector: 'app-comparison-summary',
  imports: [Amount, BidiText, ComparisonTable],
  templateUrl: './comparison-summary.html',
  styleUrl: './comparison-summary.scss',
})
export class ComparisonSummary {
  private readonly store = inject(EmployeeStore);
  protected readonly copy = copy;

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  private readonly comparison = computed(() => this.store.state()?.comparison ?? null);

  protected readonly todayNet = computed(() => this.comparison()?.today.net_pay ?? null);

  protected readonly chips = computed<readonly Chip[]>(() =>
    (this.comparison()?.alternatives ?? []).map((view) => ({
      id: `alternative-${view.position}`,
      title: `${copy.alternative} ${view.position + 1}`,
      car: carName(view),
      status: view.status,
      difference: view.difference,
    })),
  );

  private readonly missingCount = computed(() => {
    const comparison = this.comparison();
    if (comparison === null) {
      return 0;
    }
    const fields = new Set(comparison.today.missing.map((item) => item.field));
    for (const alternative of comparison.alternatives) {
      alternative.missing.forEach((item) => fields.add(item.field));
    }
    return fields.size;
  });

  protected readonly state = computed<SummaryState>(() => {
    const comparison = this.comparison();
    if (comparison === null) {
      return 'empty';
    }
    if (this.missingCount() > 0) {
      return 'missing';
    }
    return comparison.alternatives.length === 0 ? 'no_alternatives' : 'ready';
  });

  protected readonly message = computed(() => {
    switch (this.state()) {
      case 'empty':
        return copy.summary.empty;
      case 'no_alternatives':
        return copy.summary.noAlternatives;
      case 'ready':
        return copy.summary.ready;
      case 'missing': {
        const count = this.missingCount();
        return count === 1
          ? copy.summary.missingOne
          : copy.summary.missing.replace('{count}', String(count));
      }
    }
  });

  protected readonly attention = signal(false);

  constructor() {
    let wasReady: boolean | undefined;
    effect(() => {
      const ready = this.state() === 'ready';
      if (wasReady === false && ready) {
        this.attention.set(true);
      }
      if (!ready) {
        this.attention.set(false);
      }
      wasReady = ready;
    });
  }

  protected open(): void {
    this.attention.set(false);
    const dialog = this.dialog().nativeElement;
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
  }

  protected close(): void {
    const dialog = this.dialog().nativeElement;
    if (typeof dialog.close === 'function') {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  }
}
