export type User = {
  public_id: string;
  first_name: string;
  last_name: string;
  middle_name: string;
  has_full_name: boolean;
  email: string | null;
  language: string;
  theme: "system" | "light" | "dark";
};

export type CompanySummary = {
  public_id: string;
  display_name: string;
  setup: { profile: boolean; finance: boolean };
};

export type Company = CompanySummary & {
  timezone: string | null;
  main_currency: string | null;
  created_at: string;
  version: number;
  employment?: {
    id: string;
    position_code: string;
    position_name: string;
    is_top_authority: boolean;
  };
};

export type StartedFlow = { flow: string; resend_after_seconds: number; code_ttl_seconds: number };

export type Session = {
  id: string;
  created_at: string;
  last_activity_at: string;
  user_agent: string;
  ip: string | null;
  current: boolean;
};

export type ContextType = "rental" | "service" | "warehouse";

export type Location = {
  id: string;
  name: string;
  timezone: string | null;
  address: string;
  state: "draft" | "active" | "deactivated";
  version: number;
  schedule: { weekly: Record<string, { from: string; to: string }[]>; exceptions: unknown[] };
  contexts: { id: string; type: ContextType; state: string; ready: boolean }[];
};

export type CashAccount = {
  id: string;
  name: string;
  type: "cash" | "bank";
  currency: string;
  location_id: string | null;
  state: "active" | "archived";
  version: number;
};

export type Scope = "all" | string[];

export type RightSpec = {
  type: "set" | "grant" | "deny";
  set?: string;
  permission?: string;
  locations: Scope;
  contexts: Scope;
  cash_accounts: Scope;
};

export type Invitation = {
  id: string;
  state: "pending" | "accepted" | "declined" | "cancelled" | "expired";
  employment_id: string;
  position: { id: string; code: string; name: string };
  rights: RightSpec[];
  created_at: string;
  expires_at: string;
  company?: { public_id: string; display_name: string };
  data_given?: { required: string[]; optional: string[] };
};

export type Employee = {
  id: string;
  state: "invited" | "active" | "blocked" | "terminated";
  position: { id: string; code: string; name: string; is_top_authority: boolean };
  user: {
    public_id: string;
    first_name: string;
    last_name: string;
    middle_name: string | null;
    email: string | null;
  } | null;
  started_at: string | null;
  version: number;
  rights: (RightSpec & { id: string; permissions: string[]; granted_at: string })[];
  invitation: Invitation | null;
};

export type PermissionCatalog = {
  sets: { code: string; permissions: string[] }[];
  permissions: string[];
};

export type Readiness = {
  company: { items: Record<string, boolean>; ready: boolean };
  locations: {
    id: string;
    name: string;
    missing: string[];
    contexts: { id: string; type: ContextType; ready: boolean; missing: string[] }[];
  }[];
  first_operation_ready: boolean;
};

export type JournalEntry = {
  id: string;
  occurred_at: string;
  actor: { user_id: string | null; name: string; employment_id: string | null };
  action: string;
  object: { type: string; id: string };
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  details_hidden: boolean;
  reason: string;
  result: string;
};

export type PriceRange = { from: number; to: number | null; rate: string };

export type Tariff = {
  id: string;
  version: number;
  name: string;
  currency: string;
  unit: string;
  day_mode: string | null;
  all_locations: boolean;
  locations: string[];
  ranges: PriceRange[];
  state: "active" | "archived";
};

export type VehicleTerms = {
  vehicle_card_id: string;
  mileage_unit: string;
  currency: string;
  version: number;
  mileage: { mode: "unlimited" | "included"; included_per_unit: string | null; overage_price: string | null };
  deposit: { required: boolean; amount: string | null; hold_days: number | null };
};

export type Vehicle = {
  id: string;
  vehicle_id: string;
  name: string;
  relation: string;
  location_id: string | null;
  odometer_unit: "km" | "mi";
  contexts: string[];
  state: string;
  version: number;
  odometer: { value: string; unit: string; measured_at: string } | null;
  rental_readiness: { ready: boolean; missing: string[] };
};

export type ClientContact = { kind: "phone" | "email"; value: string };

export type Client = {
  id: string;
  version: number;
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string | null;
  company_name: string;
  contacts: ClientContact[];
};

export type RentalSummary = {
  id: string;
  order_id: string;
  vehicle_card_id: string;
  client_id: string;
  state: string;
  issue_location_id: string;
  actual_start: string;
  actual_end: string | null;
};

export type Calculation = {
  kind: "current" | "actual" | "preliminary";
  end?: string;
  billable_minutes: number;
  units: number;
  rate: string | null;
  base_amount: string;
  currency: string;
};

export type InspectionView = {
  id: string;
  state: "draft" | "saved" | "invalid";
  check_status: string;
  odometer: string | null;
  fuel: string | null;
};

export type Rental = RentalSummary & {
  tariff_id: string;
  issue_place_kind: "location" | "other";
  issue_place_note: string;
  calc_timezone: string;
  planned_end: string | null;
  planned_return_location_id: string | null;
  return_location_id: string | null;
  return_place_kind: "location" | "other" | null;
  return_place_note: string;
  overdue: boolean;
  inspections: { issue: InspectionView | null; return: InspectionView | null; fuel_differs: boolean | null };
  formalization: { completed_at: string | null; missing: string[] };
  tariff_snapshot: { name: string; currency: string; ranges: PriceRange[] };
  calculation: Calculation;
  version: number;
};

export type Warning = { code: string; amounts?: Record<string, string> };

export type OrderAmounts = { charged: string; paid: string; to_pay: string; overpaid: string };

export type Order = {
  id: string;
  number: number;
  client_id: string | null;
  is_internal: boolean;
  state: "open" | "ready_to_close" | "closed" | "cancelled" | "terminated";
  responsible_employment_id: string | null;
  created_at: string;
  closed_at: string | null;
  version: number;
  operations: { type: string; id: string; state: string; finished: boolean }[];
  closing_conditions: { operation: string; id: string; condition: string }[];
  amounts: Record<string, OrderAmounts>;
};

export type Payment = {
  id: string;
  number: number;
  cash_account_id: string;
  client_id: string | null;
  amount: string;
  currency: string;
  state: string;
  method: string;
  posted_at: string | null;
  allocations: { order_id: string; amount: string; purpose: string; state: string }[];
};
