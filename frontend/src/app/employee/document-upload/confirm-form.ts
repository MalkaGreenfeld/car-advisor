import { Component, computed, inject, signal } from '@angular/core';

import { DocumentCandidates, DocumentField, ProfileChanges } from '../../shared/api-models';
import { EmployeeStore, WriteResult } from '../employee-store';
import { employeeCopy } from '../employee.copy';

interface FormField {
  readonly name: DocumentField;
  readonly label: string;
  readonly detected: boolean;
  value: string;
}

const FIELD_LABELS: Partial<Record<DocumentField, string>> = {
  base_salary: employeeCopy.details.fields.base_salary.label,
  pension_rate_percent: employeeCopy.details.fields.pension_rate_percent.label,
  study_fund_rate_percent: employeeCopy.details.fields.study_fund_rate_percent.label,
  credit_points: employeeCopy.details.fields.credit_points.label,
  current_car_participation: employeeCopy.details.fields.current_car_participation.label,
};

const ALL_FIELDS: readonly DocumentField[] = [
  'base_salary',
  'pension_rate_percent',
  'study_fund_rate_percent',
  'credit_points',
  'current_car_participation',
];

@Component({
  selector: 'app-confirm-form',
  standalone: true,
  template: `
    <div class="confirm-form">
      <h3>{{ copy.formTitle }}</h3>
      <div class="fields">
        @for (field of fields(); track field.name) {
          <div class="field-row">
            <label [for]="'doc-' + field.name">{{ field.label }}</label>
            @if (field.detected) {
              <input
                [id]="'doc-' + field.name"
                type="text"
                [value]="field.value"
                (input)="onInput(field, $event)"
              />
            } @else {
              <span class="not-detected">{{ copy.notDetected }}</span>
            }
            @if (fieldError(field.name); as error) {
              <span class="field-error" role="alert">{{ error }}</span>
            }
          </div>
        }
      </div>
      @if (confirmError()) {
        <p class="form-error" role="alert">{{ copy.confirmError }}</p>
      }
      <div class="actions">
        <button type="button" class="button-primary" [disabled]="saving()" (click)="confirm()">
          {{ copy.confirm }}
        </button>
        <button type="button" (click)="cancel()">
          {{ copy.cancel }}
        </button>
      </div>
    </div>
  `,
  styles: `
    .confirm-form {
      border: 1px solid var(--color-border);
      border-radius: var(--radius);
      padding: 12px;
      margin-block: 8px;
    }

    h3 {
      font-size: 0.9375rem;
      margin: 0 0 8px;
    }

    .fields {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .field-row {
      display: flex;
      flex-direction: column;
      gap: 2px;

      label {
        font-size: 0.8125rem;
        font-weight: 500;
      }

      input {
        padding: 4px 8px;
        border: 1px solid var(--color-border);
        border-radius: var(--radius);
        font-size: 0.875rem;
      }
    }

    .not-detected {
      font-size: 0.8125rem;
      color: var(--color-text-secondary);
      font-style: italic;
    }

    .field-error {
      color: var(--color-error);
      font-size: 0.75rem;
    }

    .form-error {
      color: var(--color-error);
      font-size: 0.8125rem;
      margin: 8px 0 0;
    }

    .actions {
      display: flex;
      gap: 8px;
      margin-top: 12px;

      button {
        padding: 6px 16px;
        border-radius: var(--radius);
        font-size: 0.875rem;
        cursor: pointer;
        border: 1px solid var(--color-border);
      }
    }
  `,
})
export class ConfirmFormComponent {
  readonly store = inject(EmployeeStore);
  readonly copy = employeeCopy.document;

  readonly saving = signal(false);
  readonly confirmError = signal(false);
  readonly fieldErrors = signal<Record<string, string>>({});

  readonly fields = computed<FormField[]>(() => {
    const candidates = this.store.documentCandidates();
    if (!candidates) {
      return [];
    }
    return ALL_FIELDS.map((name) => ({
      name,
      label: FIELD_LABELS[name] ?? name,
      detected: candidates.detected.includes(name),
      value: candidates.detected.includes(name) ? String(candidates.fields[name] ?? '') : '',
    }));
  });

  onInput(field: FormField, event: Event): void {
    field.value = (event.target as HTMLInputElement).value;
  }

  fieldError(name: string): string | null {
    return this.fieldErrors()[name] ?? null;
  }

  async confirm(): Promise<void> {
    this.saving.set(true);
    this.confirmError.set(false);
    this.fieldErrors.set({});

    const changes: ProfileChanges = {};
    for (const field of this.fields()) {
      if (!field.value.trim()) {
        continue;
      }
      const parsed = this.parseValue(field.name, field.value.trim());
      if (parsed !== undefined) {
        (changes as Record<string, unknown>)[field.name] = parsed;
      }
    }

    const result: WriteResult = await this.store.confirmDocument(changes);
    this.saving.set(false);

    if (result.ok) {
      this.store.cancelDocument();
    } else if (result.fields.length > 0) {
      const errors: Record<string, string> = {};
      for (const f of result.fields) {
        errors[f.field] = f.message;
      }
      this.fieldErrors.set(errors);
    } else {
      this.confirmError.set(true);
    }
  }

  cancel(): void {
    this.store.cancelDocument();
  }

  private parseValue(name: DocumentField, text: string): unknown {
    if (
      name === 'base_salary' ||
      name === 'pension_rate_percent' ||
      name === 'study_fund_rate_percent' ||
      name === 'credit_points' ||
      name === 'current_car_participation'
    ) {
      const num = Number(text);
      return isNaN(num) ? text : num;
    }
    return text;
  }
}


