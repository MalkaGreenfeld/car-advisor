import { FieldTree } from '@angular/forms/signals';

// The message of the first error of a field, once the employee has left the field.
export function shownError(field: FieldTree<string>): string | null {
  const state = field();
  const [error] = state.errors();
  return error !== undefined && state.touched() ? (error.message ?? '') : null;
}
