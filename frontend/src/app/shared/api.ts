import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { catchError, firstValueFrom, Observable, throwError } from 'rxjs';

import {
  AlternativeRequest,
  CarKey,
  CarSearchResponse,
  CatalogResponse,
  ConversationState,
  DocumentCandidates,
  ERROR_CODES,
  ErrorBody,
  ErrorCode,
  FieldError,
  Health,
  MessageRequest,
  ProfileChanges,
  ProfileChangesRequest,
  RepAnswerRequest,
  RepQuestionsResponse,
  RepQuestionView,
} from './api-models';

const API_BASE = '/api';

export type ApiErrorCode = ErrorCode | 'network_error' | 'unexpected_response';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ApiErrorCode,
    readonly fields: readonly FieldError[] = [],
  ) {
    super(`${code} (status ${status})`);
    this.name = 'ApiError';
  }
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === 'string' && (ERROR_CODES as readonly string[]).includes(value);
}

function isFieldError(value: unknown): value is FieldError {
  return (
    isRecord(value) && typeof value['field'] === 'string' && typeof value['message'] === 'string'
  );
}

function parseErrorBody(body: unknown): ErrorBody | null {
  if (!isRecord(body)) {
    return null;
  }
  const code = body['code'];
  if (!isErrorCode(code)) {
    return null;
  }
  const fields = body['fields'];
  if (fields === undefined) {
    return { code };
  }
  return Array.isArray(fields) && fields.every(isFieldError) ? { code, fields } : null;
}

function toApiError(failure: unknown): ApiError {
  if (!(failure instanceof HttpErrorResponse)) {
    return new ApiError(0, 'unexpected_response');
  }
  // Angular also rejects a 2xx response whose body is not JSON, such as an HTML page from a proxy,
  // and keeps its 2xx status. Only status 0 means that the network failed.
  if (failure.status === 0) {
    return new ApiError(0, 'network_error');
  }
  const body = parseErrorBody(failure.error);
  return body === null
    ? new ApiError(failure.status, 'unexpected_response')
    : new ApiError(failure.status, body.code, body.fields);
}

// The ID is opaque and can come from the address bar, so it is escaped rather than trusted.
function conversationUrl(conversationId: string, suffix = ''): string {
  return `${API_BASE}/conversations/${encodeURIComponent(conversationId)}${suffix}`;
}

@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(HttpClient);

  createConversation(): Promise<ConversationState> {
    return this.request(this.http.post<ConversationState>(`${API_BASE}/conversations`, null));
  }

  getConversation(conversationId: string): Promise<ConversationState> {
    return this.request(this.http.get<ConversationState>(conversationUrl(conversationId)));
  }

  sendMessage(conversationId: string, text: string): Promise<ConversationState> {
    const body: MessageRequest = { text };
    return this.request(
      this.http.post<ConversationState>(conversationUrl(conversationId, '/messages'), body),
    );
  }

  patchProfile(conversationId: string, changes: ProfileChanges): Promise<ConversationState> {
    const body: ProfileChangesRequest = { changes };
    return this.request(
      this.http.patch<ConversationState>(conversationUrl(conversationId, '/profile'), body),
    );
  }

  replaceAlternative(
    conversationId: string,
    position: number,
    carKey: CarKey,
  ): Promise<ConversationState> {
    const body: AlternativeRequest = { kind: 'car', car_key: carKey };
    return this.request(
      this.http.put<ConversationState>(
        conversationUrl(conversationId, `/alternatives/${position}`),
        body,
      ),
    );
  }

  getCatalog(conversationId: string): Promise<CatalogResponse> {
    return this.request(
      this.http.get<CatalogResponse>(conversationUrl(conversationId, '/catalog')),
    );
  }

  searchCars(registrationYear: number, text: string): Promise<CarSearchResponse> {
    return this.request(
      this.http.get<CarSearchResponse>(`${API_BASE}/cars/search`, {
        params: { registration_year: registrationYear, text },
      }),
    );
  }

  uploadDocument(conversationId: string, file: File): Promise<DocumentCandidates> {
    const body = new FormData();
    body.append('file', file);
    return this.request(
      this.http.post<DocumentCandidates>(conversationUrl(conversationId, '/documents'), body),
    );
  }

  confirmDocument(conversationId: string, changes: ProfileChanges): Promise<ConversationState> {
    const body: ProfileChangesRequest = { changes };
    return this.request(
      this.http.post<ConversationState>(
        conversationUrl(conversationId, '/documents/confirm'),
        body,
      ),
    );
  }

  listRepQuestions(): Promise<RepQuestionsResponse> {
    return this.request(this.http.get<RepQuestionsResponse>(`${API_BASE}/rep/questions`));
  }

  answerRepQuestion(questionId: string, answer: RepAnswerRequest): Promise<RepQuestionView> {
    return this.request(
      this.http.post<RepQuestionView>(
        `${API_BASE}/rep/questions/${encodeURIComponent(questionId)}/answer`,
        answer,
      ),
    );
  }

  getHealth(): Promise<Health> {
    return this.request(this.http.get<Health>(`${API_BASE}/health`));
  }

  private request<T>(source: Observable<T>): Promise<T> {
    return firstValueFrom(
      source.pipe(catchError((failure: unknown) => throwError(() => toApiError(failure)))),
    );
  }
}
