/** The address of the form of a new inspection, with what is known about it beforehand. */
export function newPath(params: Record<string, string>): string {
  return `/inspections/new?${new URLSearchParams(params).toString()}`;
}
