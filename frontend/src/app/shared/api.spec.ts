import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { throwError } from 'rxjs';

import { ApiClient, ApiError } from './api';
import {
  CarSearchResponse,
  CatalogResponse,
  ConversationState,
  DocumentCandidates,
  EmployeeProfile,
  Health,
  RepQuestionsResponse,
  RepQuestionView,
} from './api-models';

const CAR_KEY = '2026-2026-1-01229-00004';

const profile: EmployeeProfile = {
  display_name: null,
  eligibility_group: 'B',
  base_salary: '16000',
  pay_components: [],
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

const conversationState: ConversationState = {
  id: 'c1',
  messages: [],
  profile,
  employee_group: { group: 'B', source: 'representative' },
  alternatives: [{ kind: 'car', car_key: CAR_KEY }, { kind: 'waiver' }],
  comparison: null,
  open_questions: [],
  agent: { available: true, running: false },
};

const catalog: CatalogResponse = {
  cars: [{ car_key: CAR_KEY, display_name: 'Test car', group: 'C' }],
  missing_field: null,
};

const searchResult: CarSearchResponse = {
  candidates: [],
  selection_required: false,
  none_found: true,
};

const documentCandidates: DocumentCandidates = {
  fields: { base_salary: '16000', credit_points: null },
  detected: ['base_salary'],
};

const repQuestion: RepQuestionView = {
  id: 'q1',
  question_type: 'participation',
  question_text: 'What is the participation?',
  created_at: '2026-10-13T09:12:03Z',
  employee: { display_name: 'Employee 4821', number: 4821 },
  eligibility_group: 'B',
  car: { display_name: 'Test car', car_key: CAR_KEY },
  suggested_amount: '300',
  choices: null,
  status: 'answered',
  answer: { amount: '350', effective_date: '2026-01-01', note: null, text: null, choice: null },
};

const repQuestions: RepQuestionsResponse = { questions: [repQuestion] };

const health: Health = {
  database_reachable: true,
  dataset_loaded: true,
  tax_year: 2026,
  record_count: 77988,
  catalog_warnings: [],
};

interface Route {
  readonly name: string;
  readonly call: (api: ApiClient) => Promise<unknown>;
  readonly method: string;
  readonly url: string;
  readonly body: unknown;
  readonly response: object;
}

const routes: readonly Route[] = [
  {
    name: 'createConversation',
    call: (api) => api.createConversation(),
    method: 'POST',
    url: '/api/conversations',
    body: null,
    response: conversationState,
  },
  {
    name: 'getConversation',
    call: (api) => api.getConversation('c1'),
    method: 'GET',
    url: '/api/conversations/c1',
    body: null,
    response: conversationState,
  },
  {
    name: 'sendMessage',
    call: (api) => api.sendMessage('c1', 'hello'),
    method: 'POST',
    url: '/api/conversations/c1/messages',
    body: { text: 'hello' },
    response: conversationState,
  },
  {
    name: 'patchProfile',
    call: (api) => api.patchProfile('c1', { base_salary: '16000', children_under_23: null }),
    method: 'PATCH',
    url: '/api/conversations/c1/profile',
    body: { changes: { base_salary: '16000', children_under_23: null } },
    response: conversationState,
  },
  {
    name: 'replaceAlternative',
    call: (api) => api.replaceAlternative('c1', 1, CAR_KEY),
    method: 'PUT',
    url: '/api/conversations/c1/alternatives/1',
    body: { kind: 'car', car_key: CAR_KEY },
    response: conversationState,
  },
  {
    name: 'getCatalog',
    call: (api) => api.getCatalog('c1'),
    method: 'GET',
    url: '/api/conversations/c1/catalog',
    body: null,
    response: catalog,
  },
  {
    name: 'getOccupations',
    call: (api) => api.getOccupations(),
    method: 'GET',
    url: '/api/occupations',
    body: null,
    response: { names: ['מפתח/ת תוכנה', 'מנהל/ת מוצר'] },
  },
  {
    name: 'searchCars',
    call: (api) => api.searchCars(2020, 'Model 3+'),
    method: 'GET',
    url: '/api/cars/search?registration_year=2020&text=Model%203%2B',
    body: null,
    response: searchResult,
  },
  {
    name: 'confirmDocument',
    call: (api) => api.confirmDocument('c1', { base_salary: '16000' }),
    method: 'POST',
    url: '/api/conversations/c1/documents/confirm',
    body: { changes: { base_salary: '16000' } },
    response: conversationState,
  },
  {
    name: 'listRepQuestions',
    call: (api) => api.listRepQuestions(),
    method: 'GET',
    url: '/api/rep/questions',
    body: null,
    response: repQuestions,
  },
  {
    name: 'answerRepQuestion',
    call: (api) =>
      api.answerRepQuestion('q1', {
        participation: { amount: '350', effective_date: '2026-01-01', note: 'from January' },
      }),
    method: 'POST',
    url: '/api/rep/questions/q1/answer',
    body: { participation: { amount: '350', effective_date: '2026-01-01', note: 'from January' } },
    response: repQuestion,
  },
  {
    name: 'answerRepQuestion with a choice',
    call: (api) => api.answerRepQuestion('q1', { choice: 'D' }),
    method: 'POST',
    url: '/api/rep/questions/q1/answer',
    body: { choice: 'D' },
    response: repQuestion,
  },
  {
    name: 'getHealth',
    call: (api) => api.getHealth(),
    method: 'GET',
    url: '/api/health',
    body: null,
    response: health,
  },
];

describe('ApiClient', () => {
  let api: ApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  describe('requests', () => {
    it.each(routes)('$name sends $method $url', async ({ call, method, url, body, response }) => {
      const result = call(api);

      const request = http.expectOne({ method, url });
      expect(request.request.body).toEqual(body);
      request.flush(response);

      await expect(result).resolves.toEqual(response);
    });

    it('uploadDocument sends the file as multipart form data under "file"', async () => {
      const file = new File(['%PDF-1.7'], 'payslip.pdf', { type: 'application/pdf' });
      const result = api.uploadDocument('c1', file);

      const request = http.expectOne({ method: 'POST', url: '/api/conversations/c1/documents' });
      const form = request.request.body as FormData;
      expect(form).toBeInstanceOf(FormData);
      const sent = form.get('file') as File;
      expect(sent.name).toBe('payslip.pdf');
      expect(sent.size).toBe(file.size);
      request.flush(documentCandidates);

      await expect(result).resolves.toEqual(documentCandidates);
    });

    it('escapes a conversation ID that is not a plain identifier', async () => {
      const result = api.getConversation('a/b?c');

      http
        .expectOne({ method: 'GET', url: '/api/conversations/a%2Fb%3Fc' })
        .flush(conversationState);

      await expect(result).resolves.toEqual(conversationState);
    });
  });

  describe('failures', () => {
    it('rejects with the status, the code and the fields of the error body', async () => {
      const fields = [{ field: 'base_salary', message: 'must be at least 0' }];
      const result = api.patchProfile('c1', { base_salary: '-1' });

      http
        .expectOne({ method: 'PATCH', url: '/api/conversations/c1/profile' })
        .flush({ code: 'invalid_fields', fields }, { status: 422, statusText: 'Unprocessable' });

      await expect(result).rejects.toBeInstanceOf(ApiError);
      await expect(result).rejects.toMatchObject({ status: 422, code: 'invalid_fields', fields });
    });

    it('gives an error body without fields an empty list', async () => {
      const result = api.getConversation('missing');

      http
        .expectOne('/api/conversations/missing')
        .flush({ code: 'conversation_not_found' }, { status: 404, statusText: 'Not Found' });

      await expect(result).rejects.toMatchObject({
        status: 404,
        code: 'conversation_not_found',
        fields: [],
      });
    });

    it('rejects with network_error when the request does not reach the server', async () => {
      const result = api.getHealth();

      http.expectOne('/api/health').error(new ProgressEvent('error'));

      await expect(result).rejects.toBeInstanceOf(ApiError);
      await expect(result).rejects.toMatchObject({ status: 0, code: 'network_error' });
    });

    it('rejects with unexpected_response for a 2xx body that is not JSON', async () => {
      const result = api.getHealth();

      http
        .expectOne('/api/health')
        .error(new ProgressEvent('error'), { status: 200, statusText: 'OK' });

      await expect(result).rejects.toMatchObject({ status: 200, code: 'unexpected_response' });
    });

    it.each([
      { kind: 'an HTML page', status: 502, body: '<html><body>502 Bad Gateway</body></html>' },
      { kind: 'a JSON body without a code', status: 422, body: { detail: [{ msg: 'missing' }] } },
      { kind: 'a code the client does not know', status: 500, body: { code: 'something_new' } },
      {
        kind: 'fields that are not field errors',
        status: 422,
        body: { code: 'invalid_fields', fields: ['base_salary'] },
      },
    ])('rejects with unexpected_response for $kind', async ({ status, body }) => {
      const result = api.listRepQuestions();

      http.expectOne('/api/rep/questions').flush(body, { status, statusText: 'Error' });

      await expect(result).rejects.toMatchObject({
        status,
        code: 'unexpected_response',
        fields: [],
      });
    });
  });
});

describe('ApiClient when a failure is not an HTTP response', () => {
  it('still rejects with an ApiError', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(
          withInterceptors([() => throwError(() => new Error('interceptor failed'))]),
        ),
      ],
    });

    const result = TestBed.inject(ApiClient).getHealth();

    await expect(result).rejects.toBeInstanceOf(ApiError);
    await expect(result).rejects.toMatchObject({ status: 0, code: 'unexpected_response' });
  });
});
