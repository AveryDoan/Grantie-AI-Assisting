// The rules for a merit mark, on their own so they can be tested. The AI never supplies a value: a mark starts empty.

/** A whole number from 0 to 100, typed by the officer; anything else is not a mark. */
export function validMark(text: string): number | null {
  if (!/^\d{1,3}$/.test(text.trim())) return null;
  const n = Number(text);
  return n >= 0 && n <= 100 ? n : null;
}

/** Whether this entry can be saved: a whole mark 0-100 with a reason, or Not assessed. */
export function canSave(markText: string, reason: string, notAssessed: boolean): boolean {
  if (notAssessed) return true;
  return validMark(markText) !== null && reason.trim().length > 0;
}

