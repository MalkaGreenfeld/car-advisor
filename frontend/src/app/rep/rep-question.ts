import { Component, computed, input, output } from '@angular/core';

import { RepQuestionView } from '../shared/api-models';
import { BidiText } from '../shared/bidi-text';
import { formatDate, formatDateTime, formatShekels, groupLetter } from '../shared/format';
import { AnswerForm } from './answer-form';
import { AnswerFormState } from './answer-values';
import { repCopy } from './rep.copy';

const copy = repCopy.question;

@Component({
  selector: 'app-rep-question',
  imports: [AnswerForm, BidiText],
  templateUrl: './rep-question.html',
  styleUrl: './rep-question.scss',
})
export class RepQuestion {
  readonly question = input.required<RepQuestionView>();
  // The page creates the form of every open question. An answered question has none.
  readonly formState = input<AnswerFormState>();
  readonly answered = output<RepQuestionView>();
  readonly alreadyAnswered = output<void>();

  protected readonly copy = copy;
  protected readonly storedCopy = repCopy.answer.stored;

  protected readonly typeLabel = computed(() => copy.types[this.question().question_type]);
  protected readonly statusLabel = computed(() => copy.statuses[this.question().status]);
  protected readonly created = computed(() => formatDateTime(this.question().created_at));
  protected readonly group = computed(() => {
    const group = this.question().eligibility_group;
    return group === null ? null : groupLetter(group);
  });

  // The stored answer as it is shown: an amount and its effective date, and a note if there is
  // one, or the text of any other answer.
  protected readonly stored = computed(() => {
    const answer = this.question().answer;
    if (answer === null) {
      return null;
    }
    return {
      amount: answer.amount === null ? null : formatShekels(Number(answer.amount)),
      effectiveDate: answer.effective_date === null ? null : formatDate(answer.effective_date),
      note: answer.note,
      // A choice is shown by its label, such as the Hebrew letter of a group.
      choice:
        answer.choice === null
          ? null
          : (this.question().choices?.find((item) => item.value === answer.choice)?.label ??
            answer.choice),
      text: answer.text,
    };
  });
}
