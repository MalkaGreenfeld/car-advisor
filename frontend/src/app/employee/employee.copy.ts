export const employeeCopy = {
  page: {
    title: 'יועץ הרכב',
    newConversation: 'שיחה חדשה',
    retry: 'נסו שוב',
    notFound: 'השיחה לא נמצאה.',
    updateFailed: 'עדכון המסך נכשל, ולכן מוצג המצב האחרון שנטען.',
  },
  chat: {
    heading: 'שיחה',
    listLabel: 'הודעות השיחה',
    empty: 'עדיין אין הודעות בשיחה, אפשר לכתוב הודעה כדי להתחיל.',
    senders: {
      employee: 'אני',
      advisor: 'היועץ',
    },
    typing: 'היועץ מקליד',
    messageLabel: 'הודעה ליועץ',
    placeholder: 'לדוגמה: אני בקבוצה ג ואני רוצה לבדוק רכב חשמלי',
    hint: 'Enter לשליחה, Shift+Enter לשורה חדשה.',
    send: 'שליחה',
  },
  details: {
    heading: 'הנתונים שלי',
    // In the order of the challenge script, which is the order of the panel.
    fields: {
      eligibility_group: { label: 'קבוצת זכאות' },
      base_salary: { label: 'שכר בסיס חודשי (₪)', hint: 'ברוטו, לפני ניכויים.' },
      pay_components: { label: 'רכיבי שכר נוספים' },
      pension_rate_percent: { label: 'אחוז הפרשת העובד לפנסיה' },
      study_fund_rate_percent: { label: 'אחוז הפרשת העובד לקרן השתלמות' },
      credit_points: { label: 'נקודות זיכוי', hint: 'אפשר להזין שבר עשרוני, למשל 2.25.' },
      has_current_car: { label: 'האם יש רכב חברה כיום?' },
      current_car: { label: 'הרכב הנוכחי' },
      current_car_participation: { label: 'השתתפות חודשית ברכב הנוכחי (₪)' },
      occupation: { label: 'עיסוק', hint: 'קובע אם אפשר לוותר על רכב.' },
      children_under_23: {
        label: 'מספר ילדים עד גיל 23',
        hint: 'קובע זכאות למסלול ברוכת ילדים.',
      },
      display_name: { label: 'שם', hint: 'יוצג לנציג התחבורה בשאלות שנשלחות אליו.' },
    },
    choices: {
      noGroup: 'לא נבחרה',
      noAnswer: 'לא נבחר',
      yes: 'כן',
      no: 'לא',
    },
    // The allowed values of each kind of field, worded like the server's own rejections.
    errors: {
      group: 'יש לבחור קבוצה: א, ב, ג או ד.',
      yes_no: 'יש לבחור כן או לא.',
      amount: 'יש להזין סכום בשקלים, 0 ומעלה.',
      percent: 'יש להזין אחוז בין 0 ל\u05be100.',
      points: 'יש להזין מספר נקודות זיכוי, 0 ומעלה.',
      count: 'יש להזין מספר שלם, 0 ומעלה.',
    },
    notSaved: 'לא נשמר. אפשר לנסות שוב.',
    notEntered: 'לא הוזן',
    payComponents: {
      none: 'אין רכיבי שכר נוספים',
      add: 'הוספת רכיב שכר',
      remove: 'הסרה',
      rowTitle: 'רכיב שכר {number}',
      unsaved: 'הרכיב יישמר אחרי שיוזנו סוג, תיאור וסכום.',
      fields: {
        kind: { label: 'סוג' },
        label: { label: 'תיאור' },
        amount: { label: 'סכום חודשי (₪)' },
      },
      choices: {
        noKind: 'בחירת סוג',
        commission: 'עמלה',
        bonus: 'בונוס',
      },
      errors: {
        kind: 'יש לבחור עמלה או בונוס.',
        label: 'יש להזין תיאור.',
      },
    },
    currentCar: {
      keyLabel: 'מפתח רשמי',
      change: 'שינוי',
      choose: 'בחירת רכב',
      year: 'שנת רישום',
      text: 'יצרן או דגם',
      textHint: 'אפשר להזין חלק מהשם.',
      search: 'חיפוש',
      cancel: 'ביטול',
      searching: 'מחפש…',
      candidates: 'בחירת הרכב',
      confirm: 'אישור הרכב',
      found: 'נמצאו {count} רכבים. יש לבחור את הרכב שלך.',
      foundOne: 'נמצא רכב אחד. יש לבחור אותו כדי לאשר שזה הרכב שלך.',
      noneFound: 'לא נמצא רכב מתאים. אפשר לנסות שנת רישום או שם אחרים.',
      missingInputs: 'יש להזין שנת רישום ושם יצרן או דגם.',
      searchFailed: 'החיפוש לא הצליח כרגע. אפשר לנסות שוב.',
      errors: {
        year: 'יש להזין שנת רישום בת ארבע ספרות, למשל 2020.',
        text: 'יש להזין שם יצרן או דגם.',
      },
      details: {
        modelDescription: 'תיאור דגם',
        modelCode: 'קוד דגם',
        vehicleType: 'קוד סוג רכב',
        engine: 'נפח מנוע',
        engineUnit: 'סמ״ק',
        powertrain: 'סוג הנעה',
        listPrice: 'מחיר מחירון',
      },
      // The names of the backend's POWERTRAIN_LABELS, so the chat and the panel say the same.
      powertrains: {
        electric: 'חשמלי',
        hybrid: 'היברידי',
        plug_in: 'היברידי נטען',
        conventional: 'קונבנציונלי',
      },
    },
  },
  comparison: {
    heading: 'השוואה',
  },
  notices: {
    agent_busy: 'היועץ עדיין עונה על ההודעה הקודמת. אפשר לנסות שוב בעוד רגע.',
    message_not_sent: 'ההודעה לא נשלחה. אפשר לשלוח אותה שוב.',
    change_not_saved: 'השינוי לא נשמר. אפשר לנסות שוב.',
    conversation_not_created: 'לא ניתן לפתוח שיחה חדשה. אפשר לנסות שוב.',
    // The notices that the server stores in the conversation, by their code.
    agent_unavailable:
      'היועץ אינו זמין: מפתח ה-API בהגדרות המערכת חסר או אינו תקין. ההודעה נשמרה, ואפשר להמשיך לעדכן את הנתונים ולהחליף רכב בהשוואה.',
    agent_failed: 'היועץ אינו זמין כרגע ולא ענה על ההודעה. ההודעה נשמרה, ואפשר לשלוח אותה שוב.',
    resume_failed: 'התשובה של הנציג התקבלה וההשוואה עודכנה, אבל היועץ לא הצליח להמשיך את השיחה.',
  },
} as const;

export type NoticeKey = keyof typeof employeeCopy.notices;
