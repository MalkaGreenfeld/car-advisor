import { Component, input } from '@angular/core';

import { employeeCopy } from '../employee.copy';

// The host stays in the page and only its content comes and goes, because a screen reader reports
// a change inside a status region that already exists, and often misses one that is added with it.
@Component({
  selector: 'app-typing-indicator',
  host: { role: 'status' },
  template: `
    @if (active()) {
      <span>{{ copy.typing }}</span
      ><span class="dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
    }
  `,
  styleUrl: './typing-indicator.scss',
})
export class TypingIndicator {
  readonly active = input(false);

  protected readonly copy = employeeCopy.chat;
}
