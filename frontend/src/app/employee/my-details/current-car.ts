import {
  afterNextRender,
  Component,
  computed,
  ElementRef,
  ErrorHandler,
  inject,
  Injector,
  linkedSignal,
  signal,
  viewChild,
} from '@angular/core';
import { form, FormField, schema, validate } from '@angular/forms/signals';

import { ApiClient, ApiError } from '../../shared/api';
import { CarCandidateView, Powertrain } from '../../shared/api-models';
import { BidiText } from '../../shared/bidi-text';
import { formatShekels } from '../../shared/format';
import { EmployeeStore } from '../employee-store';
import { employeeCopy } from '../employee.copy';
import { SaveQueue } from './save-queue';
import { shownError } from './shown-error';

const copy = employeeCopy.details.currentCar;
const POWERTRAINS: Record<Powertrain, string> = copy.powertrains;
const YEAR = /^\d{4}$/;

interface SearchTexts {
  year: string;
  text: string;
}

type SearchState =
  | { readonly status: 'idle' }
  | { readonly status: 'searching' }
  | { readonly status: 'none' }
  | { readonly status: 'failed'; readonly message: string }
  | {
      readonly status: 'found';
      readonly year: number;
      readonly candidates: readonly CarCandidateView[];
    };

// The candidate the employee picked, and why it could not be saved.
interface Choice {
  readonly key: string | null;
  readonly problem: string | null;
}

interface Fact {
  readonly label: string;
  readonly value: string;
  // Free text of the dataset, like the model description 3E7E, is shown as it is stored, in a plain
  // <bdi> that takes the direction of its first letter, and is not split into isolated runs.
  readonly verbatim?: true;
}

interface CandidateRow {
  readonly candidate: CarCandidateView;
  readonly name: string;
  readonly facts: readonly Fact[];
}

function yearOf(text: string): number | undefined {
  const year = text.trim();
  return YEAR.test(year) ? Number(year) : undefined;
}

// The details of Req 2.6. The manufacturer and the trade name make the name of the car.
function describeCandidate(candidate: CarCandidateView): CandidateRow {
  const details = copy.details;
  const facts: Fact[] = [
    { label: details.modelCode, value: candidate.model_code },
    { label: details.modelDescription, value: candidate.model_description, verbatim: true },
    { label: details.vehicleType, value: candidate.vehicle_type_code },
    { label: details.engine, value: `${candidate.engine_cc} ${details.engineUnit}` },
    { label: details.powertrain, value: POWERTRAINS[candidate.powertrain] },
    { label: details.listPrice, value: formatShekels(candidate.list_price) },
    { label: copy.keyLabel, value: candidate.car_key },
  ];
  return {
    candidate,
    name: `${candidate.manufacturer_name} ${candidate.trade_name}`,
    facts: facts.filter((fact) => fact.value !== ''),
  };
}

const SEARCH_SCHEMA = schema<SearchTexts>((path) => {
  validate(path.year, ({ value }) =>
    yearOf(value()) === undefined ? { kind: 'invalid', message: copy.errors.year } : null,
  );
  validate(path.text, ({ value }) =>
    value().trim() === '' ? { kind: 'required', message: copy.errors.text } : null,
  );
});

@Component({
  selector: 'app-current-car',
  imports: [BidiText, FormField],
  templateUrl: './current-car.html',
  styleUrl: './current-car.scss',
})
export class CurrentCar {
  private readonly api = inject(ApiClient);
  private readonly store = inject(EmployeeStore);
  private readonly queue = inject(SaveQueue);
  private readonly errorHandler = inject(ErrorHandler);
  private readonly injector = inject(Injector);

  protected readonly copy = copy;
  protected readonly legend = employeeCopy.details.fields.current_car.label;
  protected readonly notEntered = employeeCopy.details.notEntered;
  protected readonly searchId = 'current-car-search';

  private readonly conversationId = computed(() => this.store.state()?.id);
  private readonly opener = viewChild.required<ElementRef<HTMLButtonElement>>('opener');
  private readonly yearInput = viewChild<ElementRef<HTMLInputElement>>('yearInput');
  private readonly textInput = viewChild<ElementRef<HTMLInputElement>>('textInput');
  // Counts the searches, so that the answer to an old one is dropped.
  private searches = 0;

  protected readonly summary = computed(() => {
    const profile = this.store.state()?.profile;
    const car = profile?.current_car;
    return {
      description: car
        ? { name: `${car.manufacturer} ${car.model}`, year: String(car.registration_year) }
        : null,
      key: profile?.current_car_key ?? null,
    };
  });

  protected readonly entered = computed(() => {
    const { description, key } = this.summary();
    return description !== null || key !== null;
  });

  // The search belongs to the conversation on screen, and starts afresh in another one.
  protected readonly expanded = linkedSignal<string | undefined, boolean>({
    source: this.conversationId,
    computation: () => false,
  });
  private readonly texts = linkedSignal<string | undefined, SearchTexts>({
    source: this.conversationId,
    computation: () => ({ year: '', text: '' }),
  });
  protected readonly form = form(this.texts, SEARCH_SCHEMA);
  protected readonly yearProblem = computed(() => shownError(this.form.year));
  protected readonly textProblem = computed(() => shownError(this.form.text));

  private readonly search = linkedSignal<string | undefined, SearchState>({
    source: this.conversationId,
    computation: () => ({ status: 'idle' }),
  });
  // A new search clears the choice.
  protected readonly choice = linkedSignal<SearchState, Choice>({
    source: this.search,
    computation: () => ({ key: null, problem: null }),
  });
  protected readonly choosing = signal(false);

  protected readonly candidates = computed(() => {
    const state = this.search();
    return state.status === 'found' ? state.candidates.map(describeCandidate) : [];
  });

  // Read out by the live region of the search.
  protected readonly status = computed(() => {
    const state = this.search();
    switch (state.status) {
      case 'idle':
        return '';
      case 'searching':
        return copy.searching;
      case 'none':
        return copy.noneFound;
      case 'failed':
        return state.message;
      case 'found':
        return state.candidates.length === 1
          ? copy.foundOne
          : copy.found.replace('{count}', String(state.candidates.length));
    }
  });

  protected toggle(): void {
    if (this.expanded()) {
      this.close();
      return;
    }
    this.expanded.set(true);
    afterNextRender(() => this.yearInput()?.nativeElement.focus(), { injector: this.injector });
  }

  protected dismiss(): void {
    this.close();
    this.opener().nativeElement.focus();
  }

  protected select(key: string): void {
    this.choice.set({ key, problem: null });
  }

  protected submit(): void {
    this.form().markAsTouched();
    const year = yearOf(this.texts().year);
    const text = this.texts().text.trim();
    if (year === undefined || text === '') {
      const invalid = year === undefined ? this.yearInput() : this.textInput();
      invalid?.nativeElement.focus();
      return;
    }
    void this.run(year, text, this.conversationId());
  }

  protected choose(): void {
    const state = this.search();
    const key = this.choice().key;
    if (state.status !== 'found' || key === null) {
      return;
    }
    const candidate = state.candidates.find((item) => item.car_key === key);
    if (candidate === undefined) {
      return;
    }
    const conversationId = this.conversationId();
    this.choosing.set(true);
    this.queue.run(() => this.save(candidate, state.year, conversationId));
  }

  private close(): void {
    this.searches += 1;
    this.expanded.set(false);
    this.search.set({ status: 'idle' });
    this.form().reset({ year: '', text: '' });
  }

  private async run(year: number, text: string, conversationId: string | undefined): Promise<void> {
    const ticket = ++this.searches;
    this.search.set({ status: 'searching' });
    const next = await this.lookup(year, text);
    if (ticket === this.searches && this.isOpen(conversationId)) {
      this.search.set(next);
    }
  }

  private async lookup(year: number, text: string): Promise<SearchState> {
    try {
      const found = await this.api.searchCars(year, text);
      return found.none_found || found.candidates.length === 0
        ? { status: 'none' }
        : { status: 'found', year, candidates: found.candidates };
    } catch (failure) {
      if (!(failure instanceof ApiError)) {
        this.errorHandler.handleError(failure);
      }
      const missing = failure instanceof ApiError && failure.code === 'missing_inputs';
      return { status: 'failed', message: missing ? copy.missingInputs : copy.searchFailed };
    }
  }

  // Choosing the car is the employee's confirmation of the key (Req 2.9). The description is saved
  // with it, so that the name shown is the one of the car with that key.
  private async save(
    candidate: CarCandidateView,
    year: number,
    conversationId: string | undefined,
  ): Promise<void> {
    try {
      if (!this.isOpen(conversationId)) {
        return;
      }
      const result = await this.store.saveField({
        current_car_key: candidate.car_key,
        current_car: {
          manufacturer: candidate.manufacturer_name,
          model: candidate.trade_name,
          registration_year: year,
        },
      });
      if (!this.isOpen(conversationId)) {
        return;
      }
      if (result.ok) {
        this.dismiss();
        return;
      }
      const problem = result.fields[0]?.message ?? employeeCopy.details.notSaved;
      this.choice.update((choice) => ({ ...choice, problem }));
    } finally {
      this.choosing.set(false);
    }
  }

  // The response of an earlier conversation must not touch the one on screen.
  private isOpen(conversationId: string | undefined): boolean {
    return conversationId !== undefined && this.store.state()?.id === conversationId;
  }
}
