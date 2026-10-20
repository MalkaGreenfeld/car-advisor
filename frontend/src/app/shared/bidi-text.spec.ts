import { TestBed } from '@angular/core/testing';

import { BidiText, splitBidi } from './bidi-text';

const CHAT_LIMIT = 2000;

async function render(text: string): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(BidiText);
  fixture.componentRef.setInput('text', text);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

function isolatedRuns(host: HTMLElement): (string | null)[] {
  return Array.from(host.querySelectorAll('bdi[dir="ltr"]'), (bdi) => bdi.textContent);
}

describe('BidiText', () => {
  it.each([
    { kind: 'an amount', value: '+₪1,250' },
    { kind: 'a negative amount', value: '\u2212₪120' },
    { kind: 'a negative amount with a hyphen', value: '-₪120' },
    { kind: 'a date', value: '05/03/2026' },
    { kind: 'a time', value: '10:30' },
    { kind: 'a percentage', value: '12.5%' },
    { kind: 'a range with an en dash', value: '2020\u20132026' },
    { kind: 'an Official_Key', value: '2026-2020-1-01229-00001' },
  ])('puts $kind inside Hebrew text in a left-to-right <bdi>', async ({ value }) => {
    const host = await render(`השכר החודשי שלך משתנה ${value} לעומת היום`);

    expect(isolatedRuns(host)).toEqual([value]);
  });

  it.each([
    { kind: 'a name that ends in a digit', value: 'BMW i4' },
    { kind: 'a name with a digit inside a word', value: 'Kia EV6' },
    { kind: 'a name of three words', value: 'Tesla Model 3' },
    { kind: 'a name in capitals', value: 'MODEL 3' },
    { kind: 'a name with a number as its last word', value: 'Hyundai Ioniq 5' },
    { kind: 'a name followed by a model year', value: 'Tesla Model 3 2020' },
    { kind: 'a name followed by a number with a thousands comma', value: 'Kia EV6 20,000' },
    { kind: 'a name followed by a range with an en dash', value: 'Tesla Model 3 2020\u20132026' },
    { kind: 'a name followed by a time', value: 'Kia EV6 10:30' },
    { kind: 'a name followed by a percentage', value: 'Kia EV3 12.5%' },
    { kind: 'a number, a hyphen and a Latin word', value: '5-door' },
    { kind: 'a name with a hyphen', value: 'Mercedes-Benz EQA 250' },
    { kind: 'a name with a dot', value: 'ID.4' },
    { kind: 'a name with a diacritic', value: 'Škoda Octavia' },
    { kind: 'a name with a combining accent', value: 'S\u030Ckoda Octavia' },
    { kind: 'a name with a double space', value: 'Kia  EV6' },
    { kind: 'a name with a no-break space', value: 'Tesla\u00A0Model 3' },
    { kind: 'a code that starts a name, with a double space', value: '718  CAYMAN GTS' },
    { kind: 'a code of digits and letters', value: '3E7E' },
    { kind: 'a short code', value: '5D' },
    { kind: 'a number with a unit', value: '2.0L' },
    { kind: 'a number that starts a name', value: '296 GTB' },
  ])('keeps $kind whole in one left-to-right <bdi>: $value', async ({ value }) => {
    const text = `אפשר לבדוק את ${value} בחלופה`;

    const host = await render(text);

    expect(isolatedRuns(host)).toEqual([value]);
    expect(host.textContent).toBe(text);
  });

  it.each([
    { kind: 'a period', text: 'הדגם שנבחר הוא Tesla Model 3.', runs: ['Tesla Model 3'] },
    { kind: 'a comma', text: 'אפשר לבדוק את BMW i4, ואת האחרות', runs: ['BMW i4'] },
    { kind: 'a period after a dotted name', text: 'הדגם הוא ID.4.', runs: ['ID.4'] },
    {
      kind: 'brackets and a question mark',
      text: 'מה עם (Kia EV6) או Hyundai Ioniq 5?',
      runs: ['Kia EV6', 'Hyundai Ioniq 5'],
    },
    {
      kind: 'the hyphen of a Hebrew prefix',
      text: 'אפשר לעבור ל-Tesla Model 3 בקלות',
      runs: ['Tesla Model 3'],
    },
    {
      kind: 'a comma that a space follows, before a number',
      text: 'מה עם Kia EV6, 2020 או BMW i4?',
      runs: ['Kia EV6', '2020', 'BMW i4'],
    },
  ])('keeps $kind next to a Latin name outside the <bdi>', async ({ text, runs }) => {
    const host = await render(text);

    expect(isolatedRuns(host)).toEqual(runs);
    expect(host.textContent).toBe(text);
  });

  it.each([
    { kind: 'a year', text: 'השנה היא ב-2026 ולא קודם', runs: ['2026'] },
    { kind: 'the end of a range', text: 'יש להזין אחוז בין 0 ל-100.', runs: ['0', '100'] },
    { kind: 'an amount', text: 'הנטו עולה ב-₪350 בחודש', runs: ['₪350'] },
    { kind: 'a code', text: 'הדגמים 3E7E ו-5D נבדקו', runs: ['3E7E', '5D'] },
    { kind: 'a code that starts a name', text: 'אפשר לעבור ל-296 GTB', runs: ['296 GTB'] },
    { kind: 'an amount, with a maqaf', text: 'הנטו עולה ב\u05be₪350 בחודש', runs: ['₪350'] },
  ])('keeps the hyphen of a Hebrew prefix before $kind outside the <bdi>', async (sample) => {
    const host = await render(sample.text);

    expect(isolatedRuns(host)).toEqual(sample.runs);
    expect(host.textContent).toBe(sample.text);
  });

  it.each([
    { after: 'a space', text: 'ההפרש הוא -₪120 בחודש' },
    { after: 'a bracket', text: 'ההפרש הוא (-₪120) בחודש' },
  ])('keeps a hyphen after $after in the <bdi>, as the minus sign', async ({ text }) => {
    const host = await render(text);

    expect(isolatedRuns(host)).toEqual(['-₪120']);
    expect(host.textContent).toBe(text);
  });

  it('keeps a time whole and the colon after a number outside the <bdi>', async () => {
    const text = 'סעיף 3: הפגישה בשעה 10:30.';

    const host = await render(text);

    expect(isolatedRuns(host)).toEqual(['3', '10:30']);
    expect(host.textContent).toBe(text);
  });

  it.each([
    { side: 'before', text: 'אפשר לבדוק אתBMW i4 בחלופה' },
    { side: 'after', text: 'אפשר לבדוק את BMW i4בחלופה' },
  ])('leaves Hebrew letters that touch a Latin name $side it outside', async ({ text }) => {
    const host = await render(text);

    expect(isolatedRuns(host)).toEqual(['BMW i4']);
    expect(host.textContent).toBe(text);
  });

  it.each([
    {
      kind: 'a name and an amount',
      text: 'Tesla Model 3 עולה ₪4,500',
      runs: ['Tesla Model 3', '₪4,500'],
    },
    {
      kind: 'two names and a negative amount',
      text: 'ההפרש בין Kia EV6 לבין Hyundai Ioniq 5 הוא \u2212₪120',
      runs: ['Kia EV6', 'Hyundai Ioniq 5', '\u2212₪120'],
    },
  ])('gives $kind separate <bdi> elements when Hebrew text separates them', async (sample) => {
    const host = await render(sample.text);

    expect(isolatedRuns(host)).toEqual(sample.runs);
    expect(host.textContent).toBe(sample.text);
  });

  it.each([
    'שכר נטו ₪12,345 ביום 05/03/2026',
    '₪950 לחודש ו\u2212₪120 לשנה, רכב 2026-2020-1-01229-00001',
    'שלום, איך אפשר לעזור?',
    ' רווח בהתחלה ובסוף ',
    'שורה ראשונה  עם שני רווחים\nושורה שנייה 12',
    '',
    'אפשר לבדוק את BMW i4 בחלופה',
    ' Tesla Model 3 עולה ₪4,500 ',
    'Kia  EV6 עולה\nפחות מ-Hyundai Ioniq 5.',
    'קוד הדגם 3E7E ו-5D, וגם ID.4',
    'שיעור 6% בשעה 10:30 ל-100, ב-₪350 ובשנים 2020\u20132026',
  ])('renders the input text unchanged: %j', async (text) => {
    const host = await render(text);

    expect(host.textContent).toBe(text);
  });

  it.each([
    {
      kind: 'an amount followed by a comma',
      text: 'ההשתתפות שלך היא ₪310, והיתר ללא שינוי',
      run: '₪310',
    },
    { kind: 'an amount followed by a period', text: 'ההפרש החודשי הוא ₪350.', run: '₪350' },
    {
      kind: 'a date followed by a period',
      text: 'ההשתתפות בתוקף מתאריך 01/01/2026.',
      run: '01/01/2026',
    },
    {
      kind: 'a range followed by a comma',
      text: 'הרכבים נרשמו בשנים 2020-2026, לפי המאגר',
      run: '2020-2026',
    },
    {
      kind: 'a range that a Hebrew word follows',
      text: 'הרכבים נרשמו בשנים 2020-2026-ואילך',
      run: '2020-2026',
    },
  ])('keeps the punctuation after $kind outside the <bdi>', async ({ text, run }) => {
    const host = await render(text);

    expect(isolatedRuns(host)).toEqual([run]);
    expect(host.textContent).toBe(text);
  });

  it('renders HTML in the input as text', async () => {
    const markup = '<b>₪5</b> <img src="x" onerror="alert(1)"> <script>1</script>';

    const host = await render(markup);

    expect(host.textContent).toBe(markup);
    expect(host.querySelector('b, img, script')).toBeNull();
  });
});

describe('splitBidi', () => {
  it('returns the plain text and the isolated runs in order', () => {
    expect(splitBidi('אפשר לבדוק את BMW i4 בחלופה, ₪4,500')).toEqual([
      { text: 'אפשר לבדוק את ', isolated: false },
      { text: 'BMW i4', isolated: true },
      { text: ' בחלופה, ', isolated: false },
      { text: '₪4,500', isolated: true },
    ]);
  });

  it.each([
    {
      shape: 'Hebrew, names, amounts, dates and codes',
      unit: 'שלום Tesla Model 3 ו-BMW i4, ₪4,500 ב-05/03/2026 קוד 3E7E. ',
    },
    { shape: 'names chained by hyphens', unit: 'a-' },
    { shape: 'numbers chained by dots', unit: '1.' },
    { shape: 'numbers and names chained by spaces', unit: '1 a ' },
    { shape: 'names followed by numbers with separators', unit: 'a 1,2:3\u20134 ' },
    { shape: 'numbers chained by colons and dashes', unit: '1:\u2013' },
    { shape: 'names followed by long runs of spaces', unit: `a${' '.repeat(40)}ב ` },
  ])('splits a message of the chat limit made of $shape quickly', ({ unit }) => {
    const text = unit.repeat(Math.ceil(CHAT_LIMIT / unit.length)).slice(0, CHAT_LIMIT);

    const started = performance.now();
    const segments = splitBidi(text);
    const elapsed = performance.now() - started;

    expect(text).toHaveLength(CHAT_LIMIT);
    expect(segments.map((segment) => segment.text).join('')).toBe(text);
    expect(elapsed).toBeLessThan(100);
  });
});
