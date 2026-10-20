// Hand-written mirror of backend/src/car_advisor/api/schemas.py and the models it reuses. Names stay
// as the server sends them, in snake_case, so no conversion layer has to be kept in sync. A Decimal
// arrives as a string, a date or datetime as an ISO string, and a comparison amount as whole shekels.

export type DecimalString = string;
export type IsoDate = string;
export type IsoDateTime = string;
// The Official_Key as one text, like 2026-2020-1-01229-00001.
export type CarKey = string;

export type Group = 'A' | 'B' | 'C' | 'D';

export type Powertrain = 'electric' | 'hybrid' | 'plug_in' | 'conventional';

export type MissingField =
  | 'eligibility_group'
  | 'base_salary'
  | 'pay_components'
  | 'pension_rate'
  | 'study_fund_rate'
  | 'credit_points'
  | 'occupation'
  | 'children_under_23'
  | 'current_car'
  | 'current_car_participation'
  | 'new_car_participation'
  | 'car_group'
  | 'powertrain';

export type PayComponentKind = 'commission' | 'bonus';

export interface PayComponent {
  readonly kind: PayComponentKind;
  readonly label: string;
  readonly amount: DecimalString;
}

export interface CurrentCarDescription {
  readonly manufacturer: string;
  readonly model: string;
  readonly registration_year: number;
}

export interface EmployeeProfile {
  readonly display_name: string | null;
  readonly eligibility_group: Group | null;
  readonly base_salary: DecimalString | null;
  // An empty list means the employee has none, which counts as provided. Null means not asked yet.
  readonly pay_components: readonly PayComponent[] | null;
  readonly pension_rate_percent: DecimalString | null;
  readonly study_fund_rate_percent: DecimalString | null;
  readonly credit_points: DecimalString | null;
  readonly occupation: string | null;
  readonly children_under_23: number | null;
  readonly has_current_car: boolean | null;
  readonly current_car: CurrentCarDescription | null;
  readonly current_car_key: CarKey | null;
  readonly current_car_participation: DecimalString | null;
}

export type ProfileField = keyof EmployeeProfile;

// A null value clears its field.
export type ProfileChanges = { readonly [F in ProfileField]?: EmployeeProfile[F] };

export interface CarAlternative {
  readonly kind: 'car';
  readonly car_key: CarKey;
}

export interface WaiverAlternative {
  readonly kind: 'waiver';
}

export type Alternative = CarAlternative | WaiverAlternative;

export type MessageRole = 'employee' | 'advisor' | 'notice';

export type NoticeCode = 'agent_unavailable' | 'agent_failed' | 'resume_failed';

export interface StoredMessage {
  readonly id: number;
  readonly role: MessageRole;
  // A notice carries a code and no text, because its wording comes from the copy file.
  readonly text: string | null;
  readonly notice: NoticeCode | null;
  readonly created_at: IsoDateTime;
}

export interface CarView {
  readonly car_key: CarKey;
  readonly display_name: string | null;
  readonly group: Group | null;
}

export type EligibilityStatus = 'eligible' | 'not_eligible' | 'missing';

export interface EligibilityView {
  readonly status: EligibilityStatus;
  readonly reason: string;
  readonly is_electric: boolean | null;
}

export interface MissingView {
  readonly field: MissingField;
  readonly label: string;
  readonly sent_to_rep: boolean;
}

export interface BreakdownView {
  readonly usage_value: number;
  readonly ev_refund: number;
  readonly participation: number;
  readonly car_maintenance: number;
}

export type TodayStatus = 'calculated' | 'pending';

export interface TodayView {
  readonly status: TodayStatus;
  readonly car: CarView | null;
  readonly participation: number | null;
  readonly usage_value: number | null;
  readonly ev_refund: number | null;
  readonly car_maintenance: number | null;
  readonly net_pay: number | null;
  readonly missing: readonly MissingView[];
}

export type AlternativeStatus = 'calculated' | 'not_eligible' | 'pending';

export interface AlternativeView {
  // The index in the conversation's alternatives, which is also the column order.
  readonly position: number;
  readonly status: AlternativeStatus;
  readonly car: CarView | null;
  readonly eligibility: EligibilityView;
  readonly car_cost: number | null;
  readonly car_budget: number | null;
  readonly participation: number | null;
  readonly participation_effective_date: IsoDate | null;
  readonly employer_gap_share: number | null;
  readonly employee_gap_share: number | null;
  readonly usage_value: number | null;
  readonly ev_refund: number | null;
  readonly car_maintenance: number | null;
  readonly unused_budget: number | null;
  readonly net_pay: number | null;
  readonly difference: number | null;
  readonly breakdown: BreakdownView | null;
  readonly explanation: readonly string[] | null;
  readonly missing: readonly MissingView[];
}

// Every amount in it is a whole-shekel integer that the engine already rounded.
export interface ComparisonView {
  readonly today: TodayView;
  readonly alternatives: readonly AlternativeView[];
}

export interface OpenQuestion {
  readonly field: MissingField;
  readonly car_display_name: string | null;
  readonly car_key: CarKey | null;
}

export interface AgentView {
  // False when no API key is set, so the advisor cannot answer.
  readonly available: boolean;
  // True while a run holds the guard of the conversation, and the screen keeps polling.
  readonly running: boolean;
}

export interface ConversationState {
  readonly id: string;
  readonly messages: readonly StoredMessage[];
  readonly profile: EmployeeProfile;
  readonly alternatives: readonly Alternative[];
  readonly comparison: ComparisonView | null;
  readonly open_questions: readonly OpenQuestion[];
  readonly agent: AgentView;
}

// The longest message text the server accepts, in characters.
export const MESSAGE_MAX_LENGTH = 2000;

export interface MessageRequest {
  readonly text: string;
}

export interface ProfileChangesRequest {
  readonly changes: ProfileChanges;
}

export interface AlternativeRequest {
  readonly kind: 'car';
  readonly car_key: CarKey;
}

export interface CatalogCarView {
  readonly car_key: CarKey;
  readonly display_name: string;
  readonly group: Group;
}

export interface CatalogResponse {
  readonly cars: readonly CatalogCarView[];
  // Names the profile field to fill in when the list is empty for lack of it.
  readonly missing_field: 'eligibility_group' | null;
}

export interface CarCandidateView {
  readonly car_key: CarKey;
  readonly manufacturer_name: string;
  readonly trade_name: string;
  readonly model_code: string;
  readonly model_description: string;
  readonly vehicle_type_code: string;
  readonly engine_cc: number;
  readonly powertrain: Powertrain;
  readonly list_price: number;
}

export interface CarSearchResponse {
  readonly candidates: readonly CarCandidateView[];
  readonly selection_required: boolean;
  readonly none_found: boolean;
}

export type RepQuestionType = 'participation' | 'occupation_waiver' | 'other';

export type RepQuestionStatus = 'open' | 'answered';

export interface RepEmployee {
  readonly display_name: string;
  readonly number: number;
}

export interface RepCar {
  readonly display_name: string | null;
  readonly car_key: CarKey;
}

// A participation answer has an amount and a date, and may have a note. Any other answer is the
// text alone.
export interface StoredAnswer {
  readonly amount: DecimalString | null;
  readonly effective_date: IsoDate | null;
  readonly note: string | null;
  readonly text: string | null;
}

// It holds no conversation ID and no salary data, because Rep_Screen has no login.
export interface RepQuestionView {
  readonly id: string;
  readonly question_type: RepQuestionType;
  readonly question_text: string;
  readonly created_at: IsoDateTime;
  readonly employee: RepEmployee;
  readonly eligibility_group: Group | null;
  readonly car: RepCar | null;
  readonly status: RepQuestionStatus;
  readonly answer: StoredAnswer | null;
}

export interface RepQuestionsResponse {
  readonly questions: readonly RepQuestionView[];
}

export interface ParticipationRequest {
  readonly amount: DecimalString;
  readonly effective_date: IsoDate;
  readonly note?: string | null;
}

export interface ParticipationAnswerRequest {
  readonly participation: ParticipationRequest;
}

export interface TextAnswerRequest {
  readonly text: string;
}

export type RepAnswerRequest = ParticipationAnswerRequest | TextAnswerRequest;

export type DocumentField =
  | 'eligibility_group'
  | 'base_salary'
  | 'pay_components'
  | 'pension_rate_percent'
  | 'study_fund_rate_percent'
  | 'credit_points'
  | 'current_car'
  | 'current_car_participation';

export interface DocumentCandidates {
  // A null value is a field the document did not show.
  readonly fields: Pick<ProfileChanges, DocumentField>;
  readonly detected: readonly DocumentField[];
}

export interface Health {
  readonly database_reachable: boolean;
  readonly dataset_loaded: boolean;
  readonly tax_year: number;
  // 0 while the dataset cannot be read.
  readonly record_count: number;
}

export const ERROR_CODES = [
  'conversation_not_found',
  'agent_busy',
  'conflict',
  'database_unavailable',
  'invalid_fields',
  'missing_inputs',
  'lookup_failed',
  'invalid_answer',
  'already_answered',
  'invalid_file',
  'nothing_detected',
  'reader_failed',
  'reader_timeout',
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

export interface FieldError {
  readonly field: string;
  readonly message: string;
}

export interface ErrorBody {
  readonly code: ErrorCode;
  // Left out of the JSON, not null, when no field is to blame.
  readonly fields?: readonly FieldError[];
}
