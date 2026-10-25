export type User = {
  public_id: string;
  username: string | null;
  username_changed_at: string | null;
  first_name: string;
  last_name: string;
  middle_name: string;
  has_full_name: boolean;
  avatar: string | null;
  email: string | null;
  birth_date: string | null;
  contact_email: string;
  contact_phone: string;
  document_type: string;
  document_number: string;
  document_issued_on: string | null;
  document_expires_on: string | null;
  language: string;
  theme: "system" | "light" | "dark";
  /** Personal time zone of the interface; empty — the device's. */
  timezone: string;
};

export type CompanySummary = {
  public_id: string;
  display_name: string;
  logo: string | null;
  setup: { profile: boolean; finance: boolean };
};

export type Company = CompanySummary & {
  timezone: string | null;
  main_currency: string | null;
  created_at: string;
  version: number;
  setup_completed_at: string | null;
  context_types: ContextType[];
  // Optional profile data (§ 3.1.5).
  legal_name: string;
  country: string;
  legal_form: string;
  identifiers: { type: string; value: string }[];
  legal_address: string;
  actual_address: string;
  phone: string;
  email: string;
  website: string;
  description: string;
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
  contexts: {
    id: string;
    type: ContextType;
    state: string;
    ready: boolean;
    storage_enabled: boolean;
  }[];
};

export type CashAccount = {
  id: string;
  name: string;
  type: "cash" | "bank";
  currency: string;
  state: "active" | "archived";
  version: number;
  /** Shown with the right to view the account's money (§ 23.2.7). */
  balance?: string;
};

/** Income or expense article (§ 17.2.8): a starting one is translated by `code` while `name`
 * is empty. */
export type FinanceArticle = {
  id: string;
  kind: "income" | "expense";
  code: string | null;
  name: string | null;
  state: "active" | "archived";
  used: boolean;
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
  characteristics: VehicleCharacteristics;
  /** On a warehouse as a stock item (§ 8.1.5); null — in the garage. */
  storage: { context_id: string; location_id: string; since: string | null } | null;
  /** Declared owner (§ 8.3.13); unconfirmed until the owner answers the request. */
  owner: {
    kind: "company" | "profile" | "person" | "organization" | "client" | "none";
    lookup: string;
    name: string;
    contact: string;
    confirmed: boolean;
    company?: { public_id: string; display_name: string; legal_name: string };
    client?: { id: string; name: string };
  } | null;
};

/** Identification data and characteristics of § 8.1.8–8.1.12; all optional. */
export type VehicleCharacteristics = {
  transport_class: string;
  vehicle_type: string;
  make: string | null;
  make_name: string;
  model: string | null;
  model_name: string;
  year: number | null;
  vin: string;
  plate: string;
  plate_country: string;
  color: string;
  body_type: string;
  fuel_type: string;
  transmission: string;
  drive: string;
  engine_volume_l: string | null;
  power_hp: number | null;
  seats: number | null;
  doors: number | null;
  /** Identifiers that coincide with another vehicle: a reason to check (§ 8.6.3). */
  identifier_matches: ("vin" | "plate")[];
};

export type VehicleOptions = {
  transport_classes: { code: string; available: boolean }[];
  vehicle_types: string[];
  catalog_types: string[];
  body_types: string[];
  fuel_types: string[];
  transmissions: string[];
  drives: string[];
};

export type CatalogMake = {
  code: string;
  name: string;
  name_cyrillic: string;
  popular: boolean;
  country: string;
};

export type CatalogModel = {
  code: string;
  name: string;
  name_cyrillic: string;
  year_from: number | null;
  year_to: number | null;
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
  return_location_id: string | null;
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
  kind: "payment" | "opening" | "manual";
  direction: "in" | "out";
  article_id: string | null;
  location_id: string | null;
  context_id: string | null;
  vehicle_id: string | null;
  note: string;
  posted_at: string | null;
  allocations: { order_id: string; amount: string; purpose: string; state: string }[];
};

export type VehicleMove = {
  id: string;
  order_id: string;
  from_location_id: string;
  to_location_id: string;
  driver: { employment_id: string; name: string };
  actual_start: string;
  actual_end: string;
  note: string;
};

/** Item of the warehouse catalog (§ 11.1.11–11.1.16). */
export type WarehouseItem = {
  id: string;
  version: number;
  name: string;
  unit: "piece" | "liter" | "kilogram" | "meter";
  accounting: "individual" | "batch";
  manufacturer: string | null;
  manufacturer_other: string;
  manufacturer_name: string;
  side: string;
  longitudinal: string;
  vertical: string;
  placement: string;
  originality: string;
  condition: string;
  article: string;
  category: string;
  model_characteristic: string;
  photo: string | null;
  photo_url: string;
  comment: string;
  external_id: string;
  possible_duplicate: boolean;
  import_operation: string | null;
  state: "active" | "archived";
  applicability: { make: string; model: string | null }[];
  packagings: { id: string; name: string; quantity: string; state: string }[];
  barcodes: { id: string; code: string; packaging_id: string | null }[];
};

export type WarehouseOptions = {
  units: { code: string; accounting: string }[];
  attributes: Record<
    "side" | "longitudinal" | "vertical" | "placement" | "originality" | "condition",
    string[]
  >;
};

/** A context with storage (§ 11.1.5). */
export type Storage = {
  context_id: string;
  type: ContextType;
  state: string;
  location_id: string;
  location_name: string;
  location_deactivated: boolean;
};

/** A row of an import check (§ 22.2.2–22.2.3). */
export type ImportRow = {
  code: string;
  number: number;
  values: Record<string, string>;
  errors: { field: string; code: string }[];
  matches: { id: string; name: string; by: string }[];
  action: "create" | "skip" | "link" | "create_duplicate" | "error";
  target: string | null;
  result: "created" | "linked" | "skipped" | "conflict" | null;
  item_id: string | null;
};

export type ImportOperation = {
  id: string;
  version: number;
  kind: string;
  state: "checked" | "posted" | "cancelled";
  file_name: string;
  created_at: string;
  posted_at: string | null;
  cancelled_at: string | null;
  loaded_count: number;
  skipped_count: number;
  rows_count: number;
  warnings: { code: string; column?: string; import?: string; loaded?: number; skipped?: number }[];
  rows?: ImportRow[];
};
