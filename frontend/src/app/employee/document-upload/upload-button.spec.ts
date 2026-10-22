import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';

import { EmployeeStore } from '../employee-store';
import { UploadButtonComponent } from './upload-button';

function stubStore() {
  return {
    uploading: signal(false),
    documentError: signal<string | null>(null),
    documentCandidates: signal(null),
    upload: vi.fn(),
  };
}

describe('UploadButtonComponent', () => {
  let fixture: ComponentFixture<UploadButtonComponent>;
  let component: UploadButtonComponent;
  let store: ReturnType<typeof stubStore>;

  beforeEach(() => {
    store = stubStore();
    TestBed.configureTestingModule({
      imports: [UploadButtonComponent],
      providers: [{ provide: EmployeeStore, useValue: store }],
    });
    fixture = TestBed.createComponent(UploadButtonComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('rejects a .txt file before upload', () => {
    const file = new File(['content'], 'test.txt', { type: 'text/plain' });
    const input = fixture.nativeElement.querySelector('input[type="file"]') as HTMLInputElement;

    const event = { target: { files: [file], value: '' } } as unknown as Event;
    component.onFileSelected(event);

    expect(store.upload).not.toHaveBeenCalled();
    expect(store.documentError()).toBe('invalidFile');
  });

  it('rejects an oversized file before upload', () => {
    const data = new Uint8Array(10_485_761);
    const file = new File([data], 'big.pdf', { type: 'application/pdf' });

    const event = { target: { files: [file], value: '' } } as unknown as Event;
    component.onFileSelected(event);

    expect(store.upload).not.toHaveBeenCalled();
    expect(store.documentError()).toBe('invalidFile');
  });

  it('is disabled while uploading', () => {
    store.uploading.set(true);
    fixture.detectChanges();

    const input = fixture.nativeElement.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it('calls upload for a valid PDF', () => {
    const file = new File(['%PDF-1.7 content'], 'payslip.pdf', { type: 'application/pdf' });

    const event = { target: { files: [file], value: '' } } as unknown as Event;
    component.onFileSelected(event);

    expect(store.upload).toHaveBeenCalledWith(file);
  });
});
