import { Component, ElementRef, inject, ViewChild } from '@angular/core';

import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';

const ACCEPTED_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png'];
const MAX_SIZE = 10_485_760;

@Component({
  selector: 'app-upload-button',
  standalone: true,
  template: `
    <div class="upload-section">
      <label class="upload-label">
        <input
          #fileInput
          type="file"
          accept=".pdf,.jpg,.jpeg,.png"
          class="visually-hidden"
          [disabled]="store.uploading()"
          (change)="onFileSelected($event)"
        />
        <span class="upload-button" [class.disabled]="store.uploading()" role="button">
          {{ store.uploading() ? copy.uploading : copy.upload }}
        </span>
      </label>
      <span class="upload-hint">{{ copy.allowedTypes }}</span>
      @if (store.documentError(); as errorKey) {
        <p class="upload-error" role="alert">{{ errorMessage(errorKey) }}</p>
      }
    </div>
  `,
  styles: `
    .upload-section {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-block: 8px;
    }

    .upload-button {
      display: inline-block;
      padding: 6px 12px;
      border: 1px solid var(--color-border);
      border-radius: var(--radius);
      font-size: 0.875rem;
      cursor: pointer;
      text-align: center;
      transition: background-color 0.15s;

      &:hover:not(.disabled) {
        background-color: var(--color-surface-hover, #f5f5f5);
      }

      &.disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
    }

    .upload-hint {
      font-size: 0.75rem;
      color: var(--color-text-secondary);
    }

    .upload-error {
      color: var(--color-error);
      font-size: 0.8125rem;
      margin: 0;
    }
  `,
})
export class UploadButtonComponent {
  readonly store = inject(EmployeeStore);
  readonly copy = employeeCopy.document;

  @ViewChild('fileInput') private fileInput!: ElementRef<HTMLInputElement>;

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    // Reset so the same file can be selected again.
    input.value = '';

    if (!this.isValidExtension(file.name)) {
      this.store.documentError.set('invalidFile');
      return;
    }

    if (file.size === 0 || file.size > MAX_SIZE) {
      this.store.documentError.set('invalidFile');
      return;
    }

    void this.store.upload(file);
  }

  errorMessage(key: string): string {
    const messages: Record<string, string> = {
      invalidFile: this.copy.invalidFile,
      nothingDetected: this.copy.nothingDetected,
      readerFailed: this.copy.readerFailed,
      readerTimeout: this.copy.readerTimeout,
    };
    return messages[key] ?? this.copy.readerFailed;
  }

  private isValidExtension(filename: string): boolean {
    const dot = filename.lastIndexOf('.');
    if (dot < 0) {
      return false;
    }
    const ext = filename.slice(dot).toLowerCase();
    return ACCEPTED_EXTENSIONS.includes(ext);
  }
}
