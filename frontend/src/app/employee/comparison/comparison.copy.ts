// The names of the amounts are the words of COMPARISON_TERMS in
// backend/src/car_advisor/engine/copy_he.py, so that the chat and the table call each amount the
// same thing (Req 15.13).
export const comparisonCopy = {
  caption: 'הנטו החודשי המשוער בכל חלופה והשינוי לעומת היום, בשקלים לחודש',
  scrollLabel: 'טבלת ההשוואה',
  // No comparison is stored yet, as long as the profile holds nothing to calculate with.
  empty: 'ההשוואה תופיע כאן אחרי שיוזנו הנתונים הראשונים.',
  // The comparison has a Today column and no alternative column.
  noAlternatives: 'עדיין לא נבדקה אף חלופה, אפשר לבקש מהיועץ לבדוק רכב או ויתור על רכב.',
  picker: {
    label: 'בחירת רכב עבור',
    separator: ' · ',
    notSaved: 'לא נשמר. הרכב הקודם נשאר בעמודה.',
    // The group follows from the occupation (Req 11.18).
    needsGroup: 'יש למלא את העיסוק בנתונים שלי לפני שבוחרים רכב אחר.',
    loading: 'טוען את רשימת הרכבים…',
    failed: 'לא ניתן לטעון את רשימת הרכבים.',
  },
  itemColumn: 'פריט',
  today: 'היום',
  alternative: 'חלופה',
  // In the order of the "What to submit" items (Req 8.5).
  rows: {
    car: 'רכב',
    eligibility: 'זכאות',
    car_cost: 'עלות הרכב',
    car_budget: 'תקציב הרכב',
    unused_budget: 'תקציב שלא נוצל',
    participation: 'השתתפות',
    employee_gap_share: 'חלק העובד בפער',
    employer_gap_share: 'חלק המעסיק בפער',
    usage_value: 'שווי שימוש',
    ev_refund: 'החזר חשמל',
    car_maintenance: 'אחזקת רכב',
    net_pay: 'נטו',
    difference: 'הפרש',
    explanation: 'הסבר',
  },
  // The label of the participation part when a column has a share of the gap, which that part holds too.
  breakdownParticipationAndShare: 'מזה: השתתפות וחלק העובד בפער',
  // The parts of the difference, in the order of Req 8.6.
  breakdown: {
    usage_value: 'מזה: שווי שימוש',
    ev_refund: 'מזה: החזר חשמל',
    participation: 'מזה: השתתפות',
    car_maintenance: 'מזה: אחזקת רכב',
  },
  car: {
    none: 'אין רכב',
    waiver: 'ויתור על רכב',
    group: 'קבוצה',
    officialKey: 'מפתח רשמי',
  },
  // A calculated alternative is an eligible one.
  status: {
    calculated: 'זכאי',
    pending: 'ממתין למידע',
    not_eligible: 'לא זכאי',
  },
  missing: {
    heading: 'מידע חסר',
    sentToRep: 'נשלחה שאלה לנציג',
    notSent: 'לא נשלחה שאלה לנציג',
  },
  unusedBudgetNote: 'למידע בלבד, לא משולם כשכר',
  // Who gave the participation of a column (Req 6.25).
  participationSource: {
    employee: 'לפי העובד',
    catalog: 'לפי הקטלוג',
    representative: 'לפי נציג התחבורה',
  },
  awaitingRep: 'ממתין לאישור הנציג',
  // Under the table, while a column shows the catalog's participation (Req 6.26).
  catalogParticipationNote:
    'ההשתתפות ברכב חדש מוצגת לפי הקטלוג. הסכום הסופי נקבע מול נציג התחבורה.',
  effectiveFrom: 'בתוקף מתאריך',
  // The strip above the chat, which sums up the comparison and opens the full table.
  summary: {
    empty: 'ההשוואה תופיע אחרי שיוזנו הנתונים הראשונים',
    noAlternatives: 'עוד לא נבדקה חלופה. אפשר לבקש מהיועץ לבדוק רכב',
    missing: 'חסרים {count} נתונים להשלמת ההשוואה',
    missingOne: 'חסר נתון אחד להשלמת ההשוואה',
    ready: 'ההשוואה מוכנה',
    todayNet: 'נטו היום',
    open: 'להשוואה המלאה',
    close: 'סגירה',
    dialogTitle: 'השוואת החלופות',
  },
} as const;
