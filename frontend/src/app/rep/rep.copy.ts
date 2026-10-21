export const repCopy = {
  page: {
    title: 'שאלות לנציג התחבורה',
    refresh: 'רענון',
    retry: 'נסו שוב',
  },
  groups: {
    open: 'שאלות פתוחות',
    answered: 'שאלות שנענו',
  },
  empty: {
    open: 'אין שאלות פתוחות כרגע.',
    answered: 'עדיין לא נענו שאלות.',
  },
  updateFailed: 'עדכון הרשימה נכשל, ולכן מוצגת הרשימה האחרונה שנטענה.',
  question: {
    types: {
      participation: 'השתתפות ברכב חדש',
      occupation: 'עיסוק שאינו בטבלה',
      eligibility_group: 'קבוצת זכאות',
      other: 'שאלה כללית',
    },
    statuses: {
      open: 'פתוחה',
      answered: 'נענתה',
    },
    labels: {
      type: 'סוג השאלה',
      created: 'נשאלה בתאריך',
      employee: 'עובד',
      group: 'קבוצה',
      car: 'רכב',
      officialKey: 'מפתח רשמי',
      status: 'מצב',
    },
  },
  answer: {
    formLabel: 'מענה לשאלה',
    amount: {
      label: 'סכום ההשתתפות החודשית (₪)',
      hint: 'סכום של 0 ומעלה, עד שתי ספרות אחרי הנקודה.',
    },
    // Next to the amount field of a catalog car, which starts with that amount (Req 12.6).
    suggested: 'בקטלוג: {amount}',
    effectiveDate: { label: 'בתוקף מתאריך' },
    note: { label: 'הערה (לא חובה)' },
    choice: {
      // By the question type: the entry of the table, or the group.
      occupation: 'השורה המתאימה בטבלת העיסוקים',
      eligibility_group: 'קבוצת הזכאות של העובד/ת',
      none: 'בחירה',
    },
    text: { label: 'תשובה' },
    submit: 'שליחת תשובה',
    sending: 'שולח את התשובה…',
    saved: 'התשובה נשמרה, והשיחה של העובד ממשיכה.',
    notSaved: 'התשובה לא נשמרה. הערכים נשארו בטופס, ואפשר לנסות שוב.',
    alreadyAnswered: 'השאלה כבר נענתה. מוצגת התשובה שנשמרה.',
    errors: {
      amount: 'יש להזין סכום השתתפות בשקלים, 0 ומעלה, עד שתי ספרות אחרי הנקודה.',
      effective_date: 'יש להזין תאריך תחילה תקף.',
      note: 'ההערה ארוכה מדי, עד 500 תווים.',
      text: 'יש להזין תשובה בטקסט, עד 2,000 תווים.',
      choice: 'יש לבחור אחת מהאפשרויות ברשימה.',
    },
    stored: {
      heading: 'התשובה שנשמרה',
      amount: 'סכום',
      effectiveDate: 'בתוקף מתאריך',
      note: 'הערה',
      choice: 'הבחירה',
      text: 'תשובה',
    },
  },
} as const;
