import { Component, computed, input } from '@angular/core';

import { formatShekels, formatSigned } from '../../shared/format';

@Component({
  selector: 'app-amount',
  template: `
    @if (text(); as shown) {
      <bdi dir="ltr" [attr.data-direction]="direction()">{{ shown }}</bdi>
    }
  `,
  styleUrl: './amount.scss',
})
export class Amount {
  // An amount that is not known renders nothing.
  readonly value = input.required<number | null | undefined>();
  // A difference, and each part of one, carries its sign (Req 16.5).
  readonly signed = input(false);

  protected readonly text = computed(() => {
    const value = this.value();
    if (value === null || value === undefined) {
      return null;
    }
    return this.signed() ? formatSigned(value) : formatShekels(value);
  });

  protected readonly direction = computed(() => {
    const value = this.value();
    if (!this.signed() || !value) {
      return null;
    }
    return value > 0 ? 'increase' : 'decrease';
  });
}
