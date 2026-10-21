import {
  AlternativeView,
  ConversationState,
  EmployeeProfile,
  TodayView,
} from '../../shared/api-models';

export const EMPTY_PROFILE: EmployeeProfile = {
  display_name: null,
  eligibility_group: null,
  base_salary: null,
  pay_components: null,
  pension_rate_percent: null,
  study_fund_rate_percent: null,
  credit_points: null,
  occupation: null,
  children_under_23: null,
  has_current_car: null,
  current_car: null,
  current_car_key: null,
  current_car_participation: null,
};

export function conversationState(
  id: string,
  overrides: Partial<ConversationState> = {},
): ConversationState {
  return {
    id,
    messages: [],
    profile: EMPTY_PROFILE,
    employee_group: { group: null, source: null },
    alternatives: [],
    comparison: null,
    open_questions: [],
    agent: { available: true, running: false },
    ...overrides,
  };
}

// A calculated Today with a current car.
export function todayView(overrides: Partial<TodayView> = {}): TodayView {
  return {
    status: 'calculated',
    car: { car_key: '2026-2020-1-01229-00001', display_name: 'Kia Niro', group: 'C' },
    participation: 0,
    usage_value: 2620,
    ev_refund: 0,
    car_maintenance: 0,
    net_pay: 14235,
    missing: [],
    ...overrides,
  };
}

// A calculated electric car, whose participation came from the representative.
export function alternativeView(
  position: number,
  overrides: Partial<AlternativeView> = {},
): AlternativeView {
  return {
    position,
    status: 'calculated',
    car: { car_key: '2026-2026-1-01229-00004', display_name: 'Hyundai Ioniq 5', group: 'C' },
    eligibility: {
      status: 'eligible',
      reason: 'רכב מקבוצה ג מותר לעובד בקבוצה ג.',
      is_electric: true,
    },
    car_cost: 4500,
    car_budget: 4500,
    participation: 350,
    participation_effective_date: '2026-01-01',
    participation_source: 'representative',
    participation_awaiting_rep: false,
    employer_gap_share: null,
    employee_gap_share: null,
    usage_value: 2310,
    ev_refund: 250,
    car_maintenance: 0,
    unused_budget: null,
    net_pay: 14445,
    difference: 210,
    breakdown: { usage_value: 310, ev_refund: 250, participation: -350, car_maintenance: 0 },
    explanation: [
      'הנטו עולה ב\u05be₪210 בחודש: שווי השימוש הנמוך חוסך ₪310, החזר החשמל מוסיף ₪250,',
      'והשתתפות גבוהה יותר מורידה ₪350.',
    ],
    missing: [],
    ...overrides,
  };
}
