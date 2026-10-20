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
      occupation_waiver: 'ויתור על רכב לפי עיסוק',
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
    effectiveDate: { label: 'בתוקף מתאריך' },
    note: { label: 'הערה (לא חובה)' },
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
    },
    stored: {
      heading: 'התשובה שנשמרה',
      amount: 'סכום',
      effectiveDate: 'בתוקף מתאריך',
      note: 'הערה',
      text: 'תשובה',
    },
  },
} as const;
