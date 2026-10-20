import {
  afterRenderEffect,
  Component,
  computed,
  ElementRef,
  input,
  viewChild,
} from '@angular/core';

import { MessageRole } from '../../shared/api-models';
import { BidiText } from '../../shared/bidi-text';
import { employeeCopy } from '../employee.copy';

export interface ChatMessage {
  readonly id: number;
  readonly role: MessageRole;
  // What the list shows: the text of a message, or the wording of a notice.
  readonly text: string;
}

@Component({
  selector: 'app-message-list',
  imports: [BidiText],
  templateUrl: './message-list.html',
  styleUrl: './message-list.scss',
})
export class MessageList {
  readonly messages = input.required<readonly ChatMessage[]>();

  protected readonly copy = employeeCopy.chat;

  private readonly list = viewChild<ElementRef<HTMLElement>>('list');
  // The scroll follows the newest ID, not the array, which is a new one on every poll. Otherwise a
  // refresh with nothing new would pull the list down while someone reads an earlier message.
  private readonly newestId = computed(() => this.messages().at(-1)?.id);

  constructor() {
    afterRenderEffect(() => {
      const list = this.list()?.nativeElement;
      if (list !== undefined && this.newestId() !== undefined) {
        list.scrollTop = list.scrollHeight;
      }
    });
  }
}
