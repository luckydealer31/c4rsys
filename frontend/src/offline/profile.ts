/**
 * What the device remembers of the last sign-in, to open the interface without connection
 * (§ 10.4.3: «if the employee signed in to c4rsys on the device earlier»): the account's id,
 * name and language and the companies opened with their time zones. No password and no token
 * is kept; sending a draft needs the session to be valid on the server. The sign-out forgets it.
 */
import type { Company, User } from "../api/types";

const KEY = "c4r.offline.profile";

export type OfflineCompany = {
  public_id: string;
  display_name: string;
  timezone: string;
  /** Whether the employee may create inspections there (the server checks it again). */
  can_inspect: boolean;
};

export type OfflineProfile = {
  user_id: string;
  name: string;
  language: string;
  theme: User["theme"];
  companies: OfflineCompany[];
};

function read(): OfflineProfile | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as OfflineProfile;
    return value && typeof value.user_id === "string" ? value : null;
  } catch {
    return null;
  }
}

function write(profile: OfflineProfile): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(profile));
  } catch {
    // Private mode or full storage: offline start is then not available.
  }
}

export function offlineProfile(): OfflineProfile | null {
  return read();
}

/** After a sign-in: another account replaces the remembered one with its companies. */
export function rememberUser(user: User): void {
  const known = read();
  const name = `${user.first_name} ${user.last_name}`.trim() || user.username || user.email || user.public_id;
  write({
    user_id: user.public_id,
    name,
    language: user.language,
    theme: user.theme,
    companies: known?.user_id === user.public_id ? known.companies : [],
  });
}

export function rememberCompany(userId: string, company: Company, canInspect: boolean): void {
  const known = read();
  if (!known || known.user_id !== userId) return;
  const item: OfflineCompany = {
    public_id: company.public_id,
    display_name: company.display_name,
    timezone: company.timezone ?? "UTC",
    can_inspect: canInspect,
  };
  const others = known.companies.filter((c) => c.public_id !== company.public_id);
  write({ ...known, companies: [...others, item] });
}

export function forgetProfile(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing to forget
  }
}
