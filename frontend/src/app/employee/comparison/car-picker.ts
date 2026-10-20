import { Component, computed, inject, input, signal } from '@angular/core';

import { CarKey, CatalogCarView, Group } from '../../shared/api-models';
import { groupLetter } from '../../shared/format';
import { EmployeeStore } from '../employee-store';
import { comparisonCopy } from './comparison.copy';

const copy = comparisonCopy.picker;

// What the picker knows about the list of cars it can offer.
export type CatalogView =
  | { readonly status: 'loading' }
  | { readonly status: 'failed' }
  | { readonly status: 'no_group' }
  | { readonly status: 'ready'; readonly cars: readonly CatalogCarView[] };

// The car that the column shows now.
export interface PickedCar {
  readonly car_key: CarKey;
  readonly display_name: string | null;
  readonly group: Group | null;
}

interface Option {
  readonly key: CarKey;
  readonly label: string;
}

function optionOf(key: CarKey, name: string | null, group: Group | null): Option {
  const label = name ?? key;
  return { key, label: group === null ? label : `${label}${copy.separator}${groupLetter(group)}` };
}

@Component({
  selector: 'app-car-picker',
  templateUrl: './car-picker.html',
  styleUrl: './car-picker.scss',
})
export class CarPicker {
  private readonly store = inject(EmployeeStore);

  readonly position = input.required<number>();
  // The title of the column, which names the picker for a screen reader.
  readonly title = input.required<string>();
  readonly car = input.required<PickedCar>();
  readonly catalog = input.required<CatalogView>();

  protected readonly copy = copy;
  protected readonly notSaved = signal(false);
  private pending = false;

  protected readonly controlId = computed(() => `car-picker-${this.position()}`);
  protected readonly noteId = computed(() => `${this.controlId()}-note`);
  protected readonly enabled = computed(() => this.catalog().status === 'ready');

  // The car of the column is always an option and the selected one, also when it is not in the
  // list: a car of another group, or one that was taken out of the catalog.
  protected readonly options = computed<readonly Option[]>(() => {
    const car = this.car();
    const catalog = this.catalog();
    const listed = catalog.status === 'ready' ? catalog.cars : [];
    const options = listed.map((item) => optionOf(item.car_key, item.display_name, item.group));
    return options.some((option) => option.key === car.car_key)
      ? options
      : [optionOf(car.car_key, car.display_name, car.group), ...options];
  });

  // Why the list is not offered, in words next to the picker.
  protected readonly note = computed(() => {
    switch (this.catalog().status) {
      case 'no_group':
        return copy.needsGroup;
      case 'loading':
        return copy.loading;
      case 'failed':
        return copy.failed;
      case 'ready':
        return null;
    }
  });

  protected async change(event: Event): Promise<void> {
    const select = event.target as HTMLSelectElement;
    const chosen = select.value;
    const current = this.car().car_key;
    if (this.pending || chosen === current) {
      select.value = current;
      return;
    }
    this.pending = true;
    this.notSaved.set(false);
    try {
      const result = await this.store.replaceCar(this.position(), chosen);
      if (!result.ok) {
        // The column keeps its car, and so does the picker.
        select.value = current;
        this.notSaved.set(true);
      }
    } finally {
      this.pending = false;
    }
  }
}
