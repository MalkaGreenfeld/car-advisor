import { Component, computed, input, output } from '@angular/core';
import { FieldTree, FormField } from '@angular/forms/signals';

import { BidiText } from '../../shared/bidi-text';

export interface SelectOption {
  readonly value: string;
  readonly label: string;
}

@Component({
  selector: 'app-details-field',
  imports: [BidiText, FormField],
  templateUrl: './details-field.html',
  styleUrl: './details-field.scss',
})
export class DetailsField {
  // Also names the control, so that its label, hint and message can refer to each other.
  readonly name = input.required<string>();
  readonly label = input.required<string>();
  readonly hint = input<string>();
  readonly field = input.required<FieldTree<string>>();
  // A native select when there are options, a text input otherwise.
  readonly options = input<readonly SelectOption[]>();
  // Set for the fields that hold a number, which are typed left to right.
  readonly inputMode = input<'decimal' | 'numeric'>();
  // Values to pick from in a text input, which still takes any other text.
  readonly suggestions = input<readonly string[]>();
  // The text next to the field, and whether it says that the value is invalid.
  readonly message = input<string | null>(null);
  readonly invalid = input(false);
  // The employee left the field or pressed Enter in it.
  readonly committed = output<void>();

  protected readonly controlId = computed(() => `details-${this.name()}`);
  protected readonly hintId = computed(() => `${this.controlId()}-hint`);
  protected readonly messageId = computed(() => `${this.controlId()}-message`);
  protected readonly listId = computed(() =>
    this.suggestions() === undefined ? null : `${this.controlId()}-suggestions`,
  );
  protected readonly numeric = computed(() => this.inputMode() !== undefined);
  // The message replaces the hint here, because a message states the allowed values anyway.
  protected readonly describedBy = computed(() => {
    if (this.message() !== null) {
      return this.messageId();
    }
    return this.hint() === undefined ? null : this.hintId();
  });

  protected enter(event: Event): void {
    event.preventDefault();
    this.committed.emit();
  }
}
