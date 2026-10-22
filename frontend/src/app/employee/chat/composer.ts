import { Component, ElementRef, input, model, output, viewChild } from '@angular/core';

import { MESSAGE_MAX_LENGTH } from '../../shared/api-models';
import { employeeCopy } from '../employee.copy';

@Component({
  selector: 'app-composer',
  templateUrl: './composer.html',
  styleUrl: './composer.scss',
})
export class Composer {
  readonly draft = model('');
  // Holds the text still while a send is in flight. The draft is cleared when the send succeeds, and
  // that would drop whatever was typed in the meantime.
  readonly locked = input(false);
  readonly sendDisabled = input(false);
  readonly submitted = output<string>();

  protected readonly copy = employeeCopy.chat;
  protected readonly maxLength = MESSAGE_MAX_LENGTH;

  private readonly textarea = viewChild.required<ElementRef<HTMLTextAreaElement>>('textarea');

  protected autoGrow(element: HTMLTextAreaElement): void {
    element.style.height = 'auto';
    element.style.height = `${element.scrollHeight}px`;
  }

  protected submit(): void {
    // A button that turns disabled under a click drops the focus, so it goes back to the field.
    const textarea = this.textarea().nativeElement;
    textarea.focus();
    const text = this.draft().trim();
    if (text !== '' && !this.sendDisabled()) {
      this.submitted.emit(text);
      // Reset height after send.
      textarea.style.height = 'auto';
    }
  }
}
