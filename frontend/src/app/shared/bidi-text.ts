import { Component, computed, input } from '@angular/core';

export interface BidiSegment {
  text: string;
  isolated: boolean;
}

// To the bidi algorithm an isolate is a neutral character. A Latin name cut in two isolates, like
// "i" and "4" in "BMW i4", is therefore shown with its pieces in the wrong order inside Hebrew
// text. Each name and each code has to be one isolate, and the pattern below finds them in one pass.
const LETTER = String.raw`\p{Script=Latin}`;
// A combining accent belongs to the letter before it, in case the text is not normalized. A comma,
// a colon or an en dash between two digits belongs to the number, so that a name followed by a
// number keeps the whole number: "Kia EV6 20,000", "Tesla Model 3 2020–2026".
const LETTER_OR_DIGIT = String.raw`(?:[\p{Script=Latin}\p{M}\d]|(?<=\d)[,:\u2013](?=\d))`;
// What may sit between two letters or digits inside a name: "Mercedes-Benz", "ID.4", "R/T". A run
// of spaces counts as one joiner, because the dataset has names such as "718  CAYMAN". A no-break
// space is a space too.
const JOINER = String.raw`(?:[ \u00A0]+|[./&'-])`;

// A Latin name such as "Tesla Model 3". It starts with a letter and ends on a letter, a digit or the
// percent sign of a number, so the comma or period that closes a sentence stays outside the isolate.
const NAME = String.raw`${LETTER}${LETTER_OR_DIGIT}*(?:${JOINER}${LETTER_OR_DIGIT}+)*(?:(?<=\d)%)?`;

// A hyphen right after a letter is the hyphen of a Hebrew prefix, as in "ל-100", and not a sign.
const SIGN = String.raw`(?:[+\u2212]|(?<!\p{L})-)`;

// An amount, a date, a range, a time, a percentage or an Official_Key. It ends on a digit or on
// the percent sign, for the same reason. The percent sign stays with its digits, because outside
// the isolate it would be shown on the wrong side of them.
const NUMBER = String.raw`${SIGN}?₪?\d(?:[\d,./:\u2013-]*\d)?%?`;

// A number that runs on into a name is one code: "3E7E", "2.0L", "296 GTB".
const ISOLATED_RUN = new RegExp(`${NAME}|${NUMBER}(?:${JOINER}?${NAME})?`, 'gu');

export function splitBidi(text: string): BidiSegment[] {
  const segments: BidiSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(ISOLATED_RUN)) {
    if (match.index > cursor) {
      segments.push({ text: text.slice(cursor, match.index), isolated: false });
    }
    segments.push({ text: match[0], isolated: true });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), isolated: false });
  }
  return segments;
}

@Component({
  selector: 'app-bidi-text',
  // The template compiler turns the indentation around a bare interpolation into a space on each
  // side, so plain segments sit in an <ng-container> to render exactly the input text.
  template: `
    @for (segment of segments(); track $index) {
      @if (segment.isolated) {
        <bdi dir="ltr">{{ segment.text }}</bdi>
      } @else {
        <ng-container>{{ segment.text }}</ng-container>
      }
    }
  `,
})
export class BidiText {
  readonly text = input.required<string>();

  protected readonly segments = computed(() => splitBidi(this.text()));
}
