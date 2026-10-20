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

  protected submit(): void {
    // A button that turns disabled under a click drops the focus, so it goes back to the field.
    this.textarea().nativeElement.focus();
    const text = this.draft().trim();
    if (text !== '' && !this.sendDisabled()) {
      this.submitted.emit(text);
    }
  }
}
