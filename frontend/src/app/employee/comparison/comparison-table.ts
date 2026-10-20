import { Component, computed, inject, resource } from '@angular/core';

import { ApiClient } from '../../shared/api';
import { AlternativeView, BreakdownView, IsoDate, TodayView } from '../../shared/api-models';
import { BidiText } from '../../shared/bidi-text';
import { formatDate, groupLetter } from '../../shared/format';
import { EmployeeStore } from '../employee-store';
import { Amount } from './amount';
import { CarPicker, CatalogView } from './car-picker';
import { comparisonCopy } from './comparison.copy';

const copy = comparisonCopy;

interface Column {
  readonly id: string;
  readonly title: string;
  readonly view: TodayView | AlternativeView;
  // Set for an alternative, which alone has an eligibility, a cost, a budget, a difference, a
  // breakdown and an explanation.
  readonly alternative: AlternativeView | null;
}

function todayColumn(view: TodayView): Column {
  return { id: 'today', title: copy.today, view, alternative: null };
}

function alternativeColumn(view: AlternativeView): Column {
  return {
    id: `alternative-${view.position}`,
    title: `${copy.alternative} ${view.position + 1}`,
    view,
    alternative: view,
  };
}

@Component({
  selector: 'app-comparison-table',
  imports: [Amount, BidiText, CarPicker],
  templateUrl: './comparison-table.html',
  styleUrl: './comparison-table.scss',
})
export class ComparisonTable {
  private readonly store = inject(EmployeeStore);
  private readonly api = inject(ApiClient);

  protected readonly copy = copy;
  protected readonly breakdownParts = [
    'usage_value',
    'ev_refund',
    'participation',
    'car_maintenance',
  ] as const satisfies readonly (keyof BreakdownView)[];
  protected readonly groupLetter = groupLetter;

  // Today first, then the alternatives in the order of the conversation. Today is there as soon as
  // there is a comparison, also before any alternative, so that its missing fields show (Req 11.16).
  protected readonly columns = computed<readonly Column[]>(() => {
    const comparison = this.store.state()?.comparison;
    if (!comparison) {
      return [];
    }
    return [todayColumn(comparison.today), ...comparison.alternatives.map(alternativeColumn)];
  });

  protected readonly noAlternatives = computed(() => this.columns().length === 1);

  // The cars that the pickers offer depend on the stored group, so a new group loads them again.
  // The values below are primitives, so a reload of the state does not load the list again.
  private readonly conversationId = computed(() => this.store.state()?.id);
  private readonly group = computed(() => this.store.state()?.profile.eligibility_group ?? null);
  private readonly hasCar = computed(() =>
    (this.store.state()?.comparison?.alternatives ?? []).some((alternative) => alternative.car),
  );
  private readonly catalog = resource({
    params: () => {
      const id = this.conversationId();
      const group = this.group();
      return this.hasCar() && id !== undefined && group !== null ? { id, group } : undefined;
    },
    loader: ({ params }) => this.api.getCatalog(params.id),
  });

  protected readonly catalogView = computed<CatalogView>(() => {
    if (this.group() === null) {
      return { status: 'no_group' };
    }
    switch (this.catalog.status()) {
      case 'resolved': {
        const loaded = this.catalog.value();
        if (loaded === undefined) {
          return { status: 'loading' };
        }
        return loaded.missing_field === null
          ? { status: 'ready', cars: loaded.cars }
          : { status: 'no_group' };
      }
      case 'error':
        return { status: 'failed' };
      default:
        return { status: 'loading' };
    }
  });

  // These rows exist for some alternatives only: a car under its budget, and the family track.
  protected readonly hasUnusedBudget = computed(() =>
    this.columns().some((column) => (column.alternative?.unused_budget ?? null) !== null),
  );
  protected readonly hasGapShares = computed(() =>
    this.columns().some(
      (column) =>
        (column.alternative?.employee_gap_share ?? null) !== null ||
        (column.alternative?.employer_gap_share ?? null) !== null,
    ),
  );
  // The participation part of the difference holds the employee's share of the gap as well.
  protected breakdownLabel(part: (typeof this.breakdownParts)[number]): string {
    return part === 'participation' && this.hasGapShares()
      ? copy.breakdownParticipationAndShare
      : copy.breakdown[part];
  }

  protected effectiveFrom(date: IsoDate): string {
    return `${copy.effectiveFrom} ${formatDate(date)}`;
  }
}
