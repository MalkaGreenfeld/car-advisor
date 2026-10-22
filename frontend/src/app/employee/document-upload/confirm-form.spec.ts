import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';

import { DocumentCandidates } from '../../shared/api-models';
import { EmployeeStore, WriteResult } from '../employee-store';
import { ConfirmFormComponent } from './confirm-form';

const CANDIDATES: DocumentCandidates = {
  fields: { base_salary: '15000', pension_rate_percent: '6.5' },
  detected: ['base_salary', 'pension_rate_percent'],
};

function stubStore() {
  return {
    documentCandidates: signal<DocumentCandidates | null>(CANDIDATES),
    documentError: signal<string | null>(null),
    uploading: signal(false),
    confirmDocument: vi.fn().mockResolvedValue({ ok: true, fields: [] } as WriteResult),
    cancelDocument: vi.fn(),
  };
}

describe('ConfirmFormComponent', () => {
  let fixture: ComponentFixture<ConfirmFormComponent>;
  let component: ConfirmFormComponent;
  let store: ReturnType<typeof stubStore>;

  beforeEach(() => {
    store = stubStore();
    TestBed.configureTestingModule({
      imports: [ConfirmFormComponent],
      providers: [{ provide: EmployeeStore, useValue: store }],
    });
    fixture = TestBed.createComponent(ConfirmFormComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('shows detected fields with values and non-detected fields as not detected', () => {
    const inputs = fixture.nativeElement.querySelectorAll('input[type="text"]');
    const notDetected = fixture.nativeElement.querySelectorAll('.not-detected');

    // base_salary and pension_rate_percent are detected -> 2 inputs
    expect(inputs.length).toBe(2);
    // The remaining fields are not detected
    expect(notDetected.length).toBeGreaterThan(0);
  });

  it('confirm posts only non-empty values', async () => {
    await component.confirm();

    expect(store.confirmDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        base_salary: 15000,
        pension_rate_percent: 6.5,
      }),
    );
  });

  it('422 keeps form open with errors', async () => {
    store.confirmDocument.mockResolvedValueOnce({
      ok: false,
      fields: [{ field: 'base_salary', message: 'סכום לא תקין' }],
    } as WriteResult);

    await component.confirm();
    fixture.detectChanges();

    expect(store.cancelDocument).not.toHaveBeenCalled();
    expect(component.fieldErrors()['base_salary']).toBe('סכום לא תקין');
  });

  it('cancel clears candidates', () => {
    component.cancel();

    expect(store.cancelDocument).toHaveBeenCalled();
  });
});
