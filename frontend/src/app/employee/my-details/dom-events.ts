// What a person does in a control, for the specs of the panel.

export function typeInto(input: HTMLInputElement, text: string): void {
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

export function selectOption(select: HTMLSelectElement, value: string): void {
  select.value = value;
  select.dispatchEvent(new Event('input', { bubbles: true }));
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

export function leave(control: Element): void {
  control.dispatchEvent(new Event('blur'));
}

export function pressEnterIn(control: Element): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  control.dispatchEvent(event);
  return event;
}

export function controlLabelled(
  root: ParentNode,
  text: string,
): HTMLInputElement | HTMLSelectElement {
  const labels = Array.from(root.querySelectorAll('label'));
  const control = labels.find((label) => label.textContent?.trim() === text)?.control;
  if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement)) {
    throw new Error(`No control is labelled "${text}".`);
  }
  return control;
}

// A button is named by its aria-label when it has one, and by its text otherwise.
export function buttonNamed(root: ParentNode, name: string): HTMLButtonElement {
  const buttons = Array.from(root.querySelectorAll('button'));
  const button = buttons.find(
    (item) => (item.getAttribute('aria-label') ?? item.textContent?.trim()) === name,
  );
  if (button === undefined) {
    throw new Error(`No button is named "${name}".`);
  }
  return button;
}
