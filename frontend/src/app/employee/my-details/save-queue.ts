import { ErrorHandler, inject, Injectable } from '@angular/core';

// The saves of the panel start one after the other. A response cannot then overtake an earlier one
// on its way to the store, and a field that is left right after Enter is not sent twice.
@Injectable()
export class SaveQueue {
  private readonly errorHandler = inject(ErrorHandler);
  private tail: Promise<void> = Promise.resolve();

  run(save: () => Promise<void>): void {
    this.tail = this.tail
      .then(save)
      .catch((failure: unknown) => this.errorHandler.handleError(failure));
  }
}
