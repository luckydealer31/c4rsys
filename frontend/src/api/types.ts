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
  /** § 3.1.8: additional profile fields made public. */
  public_fields: string[];
  /** § 3.6.1: set while the company is deactivated. */
  deactivated_at: string | null;
  employment?: {
    id: string;
    position_code: string;
    position_name: string;
    is_top_authority: boolean;
    is_owner: boolean;
    has_full_rights: boolean;
  };
};

/** An owner seen by the owners (§ 3.3.6) or a party before acceptance (§ 2.2.4, § 3.1.8). */
export type PartyCard =
  | { type: "user"; public_id: string; first_name?: string; last_name?: string; username?: string }
  | {
      type: "company";
      public_id: string;
      display_name: string;
      logo?: string | null;
      country?: string;
      /** Fields the company made public (§ 3.1.8, § 3.5.3). */
      public?: Record<string, unknown>;
    };

export type ShareTransfer = {
  id: string;
  company: { public_id: string; display_name: string };
  state: "pending" | "completed" | "declined" | "cancelled" | "expired";
  sender: PartyCard;
  recipient: PartyCard;
  percent: string;
  successor: PartyCard | null;
  policy_mode: string;
  policy_percent: string | null;
  approvals: { id: string; role: string; party: string; state: string; decided_at: string | null }[];
  created_at: string;
  expires_at: string;
  decided_at: string | null;
};

export type OwnershipPolicy = {
  transfer_mode: "none" | "all" | "percent";
  transfer_percent: string | null;
  state_mode: "unset" | "none" | "all" | "percent";
  state_percent: string | null;
  version: number;
};

export type OwnerSeat = {
  id: string;
  state: "prepared" | "active" | "ended";
  position_id: string;
  is_top_authority: boolean;
  limit: RightSpec[];
  holder: {
    employment_id: string;
    state: string;
    public_id: string | null;
    first_name: string;
    last_name: string;
    rights: RightSpec[];
  } | null;
  pending_invitation: {
    id: string;
    target: PartyCard | null;
    rights: RightSpec[];
    expires_at: string;
  } | null;
  version: number;
  owner?: PartyCard;
};

export type OwnersView = {
  owners: { owner: PartyCard; percent: string; since: string; is_top_authority: boolean }[];
  history: { owner: PartyCard; percent: string; valid_from: string; valid_to: string | null }[];
  transfers: ShareTransfer[];
  policy: OwnershipPolicy;
  seats: OwnerSeat[];
  me: { is_owner: boolean; percent: string | null; is_top_authority: boolean; public_id: string };
};

export type Holding = {
  seat: OwnerSeat;
  company: PartyCard;
  percent: string | null;
  owners: { owner: PartyCard; percent: string }[];
  is_top_authority: boolean;
};

export type StateRequest = {
  id: string;
  kind: "deactivate" | "restore";
  state: string;
  created_at: string;
  decided_at: string | null;
  mode: string;
  percent: string | null;
  approved_percent: string;
  satisfied: boolean;
  owners: { owner: PartyCard; percent: string; decision: string | null }[];
  history: { party: string; decision: string; decided_at: string; counted: boolean }[];
};

export type CompanyState = {
  deactivated_at: string | null;
  blockers: string[];
  policy: { configured: string; mode: string; percent: string | null };
  request: StateRequest | null;
  can_manage: boolean;
};

export type OwnershipRequests = {
  transfers: {
    approval_id: string;
    role: string;
    party: PartyCard;
    company: PartyCard;
    transfer: ShareTransfer;
  }[];
  states: {
    request_id: string;
    kind: "deactivate" | "restore";
    party: PartyCard;
    party_key: string;
    company: PartyCard;
    request: StateRequest;
    blockers: string[];
  }[];
  outgoing: ShareTransfer[];
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

/** The request to the owner of a managed vehicle seen by the company (§ 8.3.1, § 8.3.14). */
export type OwnerRequest = {
  id: string;
  state: "open" | "awaiting_rest" | "complete" | "cancelled" | "superseded" | "expired";
  target: string;
  by_phone: boolean;
  created_at: string;
  expires_at: string;
  answers: { question: "link" | "ownership" | "management"; answer: boolean; at: string; by: string }[];
  ownership_dispute: boolean;
  /** Why the request was closed by the company, e.g. the declared owner changed. */
  closed_reason?: string;
};

/** The same request seen by the owner. */
export type IncomingOwnerRequest = {
  id: string;
  state: "open" | "complete" | "cancelled" | "superseded" | "expired";
  company: { public_id: string; display_name: string };
  vehicle: { vehicle_id: string; make: string; model: string; year: number | null; plate: string; vin: string };
  created_at: string;
  expires_at: string;
  answers: Partial<Record<"link" | "ownership" | "management", boolean>>;
};

/** A request to link a card with a c4rsys profile and the link it became (§ 6.2.5–6.3, К4). */
export type ProfileLink = {
  id: string;
  state: "pending" | "accepted" | "declined" | "cancelled" | "expired" | "broken";
  profile: "person" | "company";
  target: string;
  by_phone: boolean;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  broken_at: string | null;
  broken_initiator: "" | "company" | "client";
  linked_profile: { public_id: string; name: string } | null;
  shared: Record<string, unknown>;
  shared_at: string | null;
};

/** The same request seen by its recipient: who asks and what may be given. */
export type IncomingLink = {
  id: string;
  state: ProfileLink["state"];
  company: { public_id: string; display_name: string };
  card_type: "client" | "counterparty";
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  broken_at: string | null;
  available: Record<string, unknown>;
  shared: Record<string, unknown>;
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

export type OrgNode = { type: "employee" | "department"; id: string };

export type Employee = {
  id: string;
  state: "invited" | "active" | "blocked" | "terminated";
  position: {
    id: string;
    code: string;
    name: string;
    is_top_authority: boolean;
    is_dedicated?: boolean;
  };
  user: {
    public_id: string;
    first_name: string;
    last_name: string;
    middle_name: string | null;
    email: string | null;
    photo?: string | null;
    phone?: string | null;
  } | null;
  started_at: string | null;
  /** Work contacts, kept apart from the contacts used to sign in (§ 5.1.7). */
  work_email: string;
  work_phone: string;
  responsibles: OrgNode[];
  departments: { id: string; name: string }[];
  termination: { at: string; reason: string } | null;
  version: number;
  rights: (RightSpec & { id: string; permissions: string[]; granted_at: string })[];
  invitation: Invitation | null;
};

/** An entry of the positions handbook (§ 5.1.5). */
export type PositionEntry = {
  id: string;
  code: string;
  name: string;
  is_dedicated: boolean;
  employees: number;
};

/** The organisation structure (§ 5.2). */
export type OrgStructure = {
  top: string | null;
  departments: { id: string; name: string; parent: OrgNode; members: string[]; version: number }[];
  links: { subordinate: string; responsible: OrgNode }[];
  employees: {
    id: string;
    name: string;
    state: Employee["state"];
    position: { id: string; code: string; name: string };
    is_top_authority: boolean;
    linked: boolean;
  }[];
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
  /** Completion of each section of the wizard (§ 22.1.4–22.1.12). */
  sections: Record<string, boolean>;
  choices: { employees?: "alone"; documents?: "none" };
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
  day_boundary: string | null;
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
  /** Cost of acquisition of an own vehicle, for payback (§ 21.4.2; decision О-3). */
  acquisition?: { cost: string; currency: string } | null;
  /** Declared owner (§ 8.3.13); unconfirmed until the owner answers the request. */
  owner: {
    kind: "company" | "profile" | "person" | "organization" | "client" | "none";
    lookup: string;
    name: string;
    contact: string;
    /** Three «yes» to the request of § 8.3.1. */
    confirmed: boolean;
    ownership_confirmed?: boolean;
    /** A refusal of the link or the management (§ 8.3.3). */
    conflict?: boolean;
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
  body_number: string;
  chassis_number: string;
  frame_number: string;
  engine_number: string;
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
  identifier_matches: ("vin" | "plate" | "body_number" | "chassis_number" | "frame_number")[];
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

/** A messenger may be bound to a phone; without it the phone is called or texted. */
export type Messenger =
  | ""
  | "whatsapp"
  | "telegram"
  | "line"
  | "wechat"
  | "viber"
  | "signal"
  | "kakaotalk"
  | "zalo"
  | "facebook_messenger"
  | "other";

export type ClientContact = { kind: "phone" | "email"; value: string; messenger?: Messenger };

export type Client = {
  id: string;
  version: number;
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  birth_date: string | null;
  company_name: string;
  address: string;
  comment: string;
  external_id: string;
  possible_duplicate: boolean;
  contacts: ClientContact[];
};

export type RentalSummary = {
  id: string;
  order_id: string;
  vehicle_card_id: string;
  client_id: string | null;
  state: string;
  issue_location_id: string | null;
  return_location_id: string | null;
  actual_start: string | null;
  actual_end: string | null;
  planned_end?: string | null;
  deposit?: DepositInfo;
  booking?: Booking | null;
  issue_exception?: IssueException | null;
};

/** A requirement of the issue left unmet by a strict policy (§ 14.3.7). */
export type IssueRequirement = { code: "payment" | "debt" | "documents"; amounts?: Record<string, string> };

/** Permitted in advance, or recorded after the transfer and waiting for a decision (§ 14.3.7). */
export type IssueException = {
  mode: "advance" | "after_fact";
  state: "granted" | "applied" | "pending" | "confirmed" | "rejected";
  requirements: IssueRequirement["code"][];
  reason: string;
  decision_reason: string;
  decided_at: string | null;
  created_at: string;
  version: number;
};

/** A booking (§ 14.1): own number; a rental in state «booked» until the issue. */
export type Booking = {
  number: number;
  planned_start: string;
  contact: string;
  comment: string;
  auto_cancel_at: string | null;
  auto_cancel_rule: { enabled?: boolean; after_minutes?: number | null; source?: string } | null;
  cancelled_at: string | null;
  cancel_reason: string;
  deposit_override: { required: boolean; amount: string | null; reason: string } | null;
  delay_conflict: { rental: string; planned_end: string | null; overdue: boolean } | null;
  vehicle_location_id: string | null;
  client_missing: boolean;
};

export type BookingConflict = {
  kind: "booking" | "rental" | "service" | "delivery";
  id: string;
  number?: number;
  state?: string;
  /** Shown, not blocking: a delivery of the vehicle (§ 14.6.3). */
  warning?: boolean;
};

/** The deposit of a rental in its currency (§ 14.7.3). */
export type DepositInfo = {
  currency: string;
  required: string;
  received: string;
  held: string;
  claimed: string;
  offset: string;
  refunded: string;
  to_balance: string;
  missing: string;
  conflict: boolean;
  waived: boolean;
  hold_days: number | null;
  release_at: string | null;
  entries: {
    id: string;
    kind: "received" | "to_balance" | "hold" | "release" | "offset" | "refund" | "waiver";
    amount: string;
    money_amount: string | null;
    money_currency: string | null;
    order_id: string | null;
    reason: string;
    automatic: boolean;
    created_at: string;
  }[];
};

export type Calculation = {
  kind: "current" | "actual" | "preliminary";
  end?: string;
  unit?: string | null;
  billable_minutes: number;
  free_minutes?: number;
  units: number;
  rate: string | null;
  base_amount: string;
  currency: string;
};

/** Free time entered by hand (§ 13.3.2): by its end or by a number of tariff units. */
export type FreeInterval = { id: string; start: string; end: string; units: number | null; comment: string; version: number };

/** Mileage of the rental against its included limit (§ 13.4.1–13.4.4). */
export type Mileage = {
  mode: "unlimited" | "included";
  unit: string;
  distance: string | null;
  included?: string | null;
  overage?: string;
  amount?: string;
  currency?: string;
};

/** A manual charge of the rental (§ 13.5.1), cancelled with a reason. */
export type ManualCharge = {
  id: string;
  kind: "fuel" | "early_return" | "overdue" | "other";
  amount: string;
  currency: string;
  reason: string;
  created_at: string;
  cancelled_at: string | null;
  cancel_reason: string;
};

export type InspectionView = {
  id: string;
  state: "draft" | "saved" | "invalid";
  check_status: string;
  odometer: string | null;
  fuel: string | null;
};

export type Rental = RentalSummary & {
  tariff_id: string | null;
  issue_place_kind: "location" | "other" | "";
  issue_place_note: string;
  issue_place_address: string;
  issue_place_lat: string | null;
  issue_place_lng: string | null;
  calc_timezone: string;
  planned_end: string | null;
  planned_return_location_id: string | null;
  return_location_id: string | null;
  return_place_kind: "location" | "other" | null;
  return_place_note: string;
  overdue: boolean;
  inspections: {
    issue: InspectionView | null;
    return: InspectionView | null;
    fuel_differs: boolean | null;
    // § 10.3.3: kinds whose required check was not done and has no recorded reason.
    check_conflicts: ("issue" | "return")[];
    missing_reasons: { kind: "issue" | "return"; reason: string; created_by_user_id: string; created_at: string }[];
  };
  formalization: { completed_at: string | null; missing: string[] };
  tariff_snapshot: { name: string; currency: string; ranges: PriceRange[] } | null;
  calculation: Calculation | null;
  free_intervals?: FreeInterval[];
  mileage?: Mileage | null;
  manual_charges?: ManualCharge[];
  services?: ServiceLine[];
  services_not_allowed?: string[];
  reopened_at?: string | null;
  version: number;
};

/** An additional service of the rental catalog (§ 13.4.5–13.4.7): once, by quantity or by time. */
export type ExtraService = {
  id: string;
  name: string;
  pricing: "once" | "quantity" | "time";
  unit: "minute" | "hour" | "day" | "month" | null;
  price: string;
  currency: string;
  all_locations: boolean;
  locations: string[];
  vehicles: string[];
  tariffs: string[];
  state: "active" | "archived";
  version: number;
};

export type ServiceChoice = Pick<ExtraService, "id" | "name" | "pricing" | "unit" | "price" | "currency">;

/** A service in an order: linked to a rental or standalone (§ 13.4.5, § 13.4.9, § 13.4.11). */
export type ServiceLine = {
  id: string;
  order_id: string;
  service_id: string;
  name: string;
  pricing: ExtraService["pricing"];
  unit: ExtraService["unit"];
  price: string;
  currency: string;
  quantity: number | null;
  rental_id: string | null;
  origin_rental_id: string | null;
  location_id: string | null;
  time_zone: string;
  period: { start: string; end: string | null; kind: string } | null;
  state: "planned" | "started" | "completed" | "cancelled";
  actual_start: string | null;
  actual_end: string | null;
  unlinked_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string;
  calculation: {
    kind: string;
    amount: string;
    currency: string;
    units: number | null;
    billable_minutes: number | null;
    free_minutes: number | null;
  } | null;
  free_intervals: { id: string; start: string; end: string; units: number | null; comment: string }[];
  finished: boolean;
  version: number;
};

export type Warning = {
  code: string;
  amounts?: Record<string, string>;
  documents?: { template?: string; kind_code?: string | null; kind_name?: string | null }[];
};

export type OrderAmounts = { charged: string; paid: string; to_pay: string; overpaid: string };

export type Order = {
  id: string;
  number: number;
  client_id: string | null;
  is_internal: boolean;
  state: "preliminary" | "open" | "ready_to_close" | "closed" | "cancelled" | "terminated";
  responsible_employment_id: string | null;
  created_at: string;
  closed_at: string | null;
  cancelled_at?: string | null;
  cancel_reason?: string;
  version: number;
  operations: { type: string; id: string; state: string; finished: boolean }[];
  closing_conditions: { operation: string; id: string; condition: string }[];
  amounts: Record<string, OrderAmounts>;
  estimated: Record<string, string>; // an order without a client: not charged yet (Р25)
};

/** A rental offered in a preliminary order: it holds nothing until the order is formed (З2). */
export type RentalProposal = {
  id: string;
  vehicle_card_id: string;
  tariff_id: string | null;
  tariff_name: string | null;
  issue_location_id: string | null;
  planned_return_location_id: string | null;
  calc_timezone: string;
  planned_start: string | null;
  planned_end: string | null;
  estimate: { amount: string; currency: string } | null;
  conflicts: BookingConflict[];
  version: number;
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
  kind: "payment" | "opening" | "manual" | "transfer" | "supplier" | "refund" | "reversal";
  direction: "in" | "out";
  counterparty_id: string | null;
  /** The client was corrected after the payment (§ 17.2.12); `client_id` is the current one. */
  client_changed: boolean;
  /** A reversal names the record it cancels; a reversed record names its reversal (§ 23.2.7). */
  reverses_id: string | null;
  reversed_by_id: string | null;
  reason: string;
  /** A refund in another currency: what left the client's funds (§ 17.2.17). */
  funds_amount: string | null;
  funds_currency: string | null;
  applied_rate: string | null;
  supplier_links: { purchase_id: string | null; receipt_id: string | null; amount: string; state: string }[];
  /** Split of an expense between vehicles and orders (§ 17.3.3). */
  shares: { vehicle_id: string | null; order_id: string | null; amount: string }[];
  article_id: string | null;
  location_id: string | null;
  context_id: string | null;
  vehicle_id: string | null;
  note: string;
  posted_at: string | null;
  allocations: {
    id: string;
    order_id: string;
    amount: string;
    purpose: string;
    state: string;
    /** In the order's currency when it differs from the payment's (§ 17.1.4). */
    settled_amount: string | null;
    settled_currency: string | null;
    applied_rate: string | null;
  }[];
  /** The other side of a transfer between own accounts (§ 17.1.6). */
  transfer: {
    id: string;
    other_account_id: string;
    other_amount: string;
    other_currency: string;
    applied_rate: string;
    suggested_rate: string | null;
    rate_manual: boolean;
  } | null;
};

/** Balances of accounts valued in the main currency by the current rates (§ 17.1.2). */
export type AccountsTotal = {
  amount: string;
  currency: string;
  missing: string[];
  rates_at: string | null;
  stale: boolean;
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
  part_category: string | null;
  part_subcategory: string | null;
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

/** The warehouse of a location — the only place stock is kept (decision of 25.10.2026). */
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
  action: "create" | "skip" | "link" | "create_duplicate" | "create_packaging" | "error";
  target: string | null;
  /** Values the module derived; for initial stock a packaging that does not exist yet. */
  extra?: {
    item_name?: string;
    packaging_missing?: { quantity: string; name: string; unit: string };
  };
  /** The group of rows loaded only together: the code of a new lot in the file (§ 22.2.2). */
  group?: string | null;
  result: "created" | "linked" | "skipped" | "conflict" | null;
  /** The ID points to one record, other identifiers to another (§ 22.2.3). */
  conflict?: boolean;
  /** The new packaging was confirmed; a row with matches still needs its own choice (§ 22.4.2). */
  packaging_confirmed?: boolean;
  record_id: string | null;
};

export type ImportKind = "items" | "vehicles" | "clients" | "counterparties" | "stock";

export type ImportOperation = {
  id: string;
  version: number;
  kind: ImportKind;
  state: "checked" | "posted" | "cancelled";
  file_name: string;
  created_at: string;
  posted_at: string | null;
  cancelled_at: string | null;
  loaded_count: number;
  skipped_count: number;
  rows_count: number;
  warnings: {
    code: string;
    column?: string;
    import?: string;
    loaded?: number;
    skipped?: number;
    number?: number;
    row?: string;
    rows?: number;
  }[];
  uploads: { file_name: string; at: string }[];
  files_kept_until: string;
  files_removed: boolean;
  rows?: ImportRow[];
};

/** A counterparty — a supplier of the company (§ 6.1.4), a list apart from clients. */
export type Counterparty = {
  id: string;
  version: number;
  name: string;
  kind: "person" | "company";
  first_name: string;
  last_name: string;
  middle_name: string;
  company_name: string;
  comment: string;
  registration: string;
  address: string;
  category: string;
  external_id: string;
  possible_duplicate: boolean;
  state: "active" | "archived";
  contacts: ClientContact[];
};

export type ItemBrief = {
  id: string;
  name: string;
  unit: WarehouseItem["unit"];
  accounting: "individual" | "batch";
  article: string;
};

/** A receipt to the warehouse of a location (§ 11.2.7). */
export type Receipt = {
  id: string;
  number: number;
  version: number;
  state: "draft" | "posted";
  source: "purchase" | "initial";
  context: { id: string; location_id: string; location_name: string };
  supplier: { id: string; name: string } | null;
  purchase: { id: string; number: number } | null;
  document_id: string | null;
  currency: string;
  comment: string;
  received_at: string;
  created_at: string;
  posted_at: string | null;
  total_cost: string;
  cost_incomplete: boolean;
  lines: ReceiptLine[];
};

export type ReceiptLine = {
  id: string;
  item: ItemBrief;
  quantity: string;
  packaging_id: string | null;
  packaging_quantity: string | null;
  packs: number | null;
  expected_quantity: string | null;
  discrepancy: string | null;
  unit_cost: string | null;
  total_cost: string | null;
  tags: string[];
  serial_numbers: string[];
  lot: { id: string; public_id: string } | null;
  new_lot: boolean;
  lot_external_number: string;
  lot_production_date: string | null;
  lot_expiry_date: string | null;
  purchase_line_id: string | null;
  assets: string[];
  posted_quantity: string;
  returned_quantity: string;
  returnable_quantity: string;
  lot_id: string | null;
  asset_refs: { id: string; public_id: string; state: string }[];
  corrections: {
    quantity_before: string;
    quantity_after: string;
    unit_cost_before: string | null;
    unit_cost_after: string | null;
    reason: string;
    created_at: string;
  }[];
};

/** A purchase received in one or several receipts (§ 11.2.11). */
export type Purchase = {
  id: string;
  number: number;
  version: number;
  state: "open" | "completed";
  supplier: { id: string; name: string } | null;
  currency: string;
  comment: string;
  created_at: string;
  lines: {
    id: string;
    item: ItemBrief;
    ordered_quantity: string;
    received_quantity: string;
    cancelled_quantity: string;
    expected_quantity: string;
    unit_cost: string | null;
  }[];
  receipts: { id: string; number: number; state: Receipt["state"] }[];
};

/** A row of the stock: an item with its quantity by warehouse and joint tags (§ 11.1.10). */
export type StockRow = {
  item: ItemBrief;
  quantity: string;
  tags: string[];
  below_minimum: boolean;
  places: { context_id: string; location_name: string; quantity: string; tags: string[]; minimum: string | null }[];
};

/** The expanded stock of an item: assets or lots per warehouse (§ 11.1.10, § 11.2.13). */
export type ItemStock = {
  item_id: string;
  places: {
    context_id: string;
    location_name: string;
    quantity: string;
    assets: {
      id: string;
      public_id: string;
      serial_number: string;
      tags: string[];
      purchase_cost: string | null;
      currency: string;
      receipt_number: number | null;
      received_at: string;
    }[];
    lots: {
      id: string;
      public_id: string;
      external_number: string;
      production_date: string | null;
      expiry_date: string | null;
      expired: boolean;
      tags: string[];
      quantity: string;
      currency: string;
      stock: { packaging_id: string | null; packaging_quantity: string | null; packs: number | null; quantity: string }[];
      opened_packages: { id: string; packaging_quantity: string; remaining: string; opened_at: string }[];
      parent: string | null;
      layers: {
        receipt_number: number | null;
        received_at: string;
        received_quantity: string;
        remaining: string;
        unit_cost: string | null;
      }[];
    }[];
  }[];
};

export type LotChoice = {
  id: string;
  public_id: string;
  external_number: string;
  production_date: string | null;
  expiry_date: string | null;
  matches: boolean;
};

export type SupplierArticle = {
  id: string;
  supplier: { id: string; name: string };
  article: string;
  supplier_name: string;
};

export type WriteOffReason = { id: string; code: string; name: string; state: "active" | "archived" };

/** An operation with the stock (§ 11.3–11.6). */
export type StockOperation = {
  id: string;
  number: number;
  version: number;
  kind: "issue" | "consume" | "move" | "write_off" | "repack" | "sale" | "supplier_return";
  state: "done" | "in_transit" | "discrepancy" | "received";
  context: { id: string; location_name: string };
  target_context: { id: string; location_name: string } | null;
  vehicle: { id: string; name: string } | null;
  reason: WriteOffReason | null;
  parent_id: string | null;
  receipt: { id: string; number: number } | null;
  children: { id: string; number: number; kind: StockOperation["kind"] }[];
  photos: string[];
  currency: string;
  comment: string;
  created_at: string;
  received_at: string | null;
  lines: {
    id: string;
    item: ItemBrief;
    asset: { id: string; public_id: string } | null;
    lot: { id: string; public_id: string } | null;
    packaging_quantity: string | null;
    opened_package_id: string | null;
    output: boolean;
    quantity: string;
    returned_quantity: string;
    consumed_quantity: string;
    outstanding: string;
    received_quantity: string;
    settled_quantity: string;
    shortage: string;
    target_lot: string | null;
    expired_confirmed: boolean;
    cost: string | null;
    cost_known: boolean;
  }[];
};

/** What can be taken from a warehouse: units or lots with their forms (§ 11.3.1). */
export type Sources = {
  assets: { id: string; public_id: string; serial_number: string; tags: string[] }[];
  lots: {
    id: string;
    public_id: string;
    external_number: string;
    expiry_date: string | null;
    expired: boolean;
    forms: { packaging_id: string | null; packaging_quantity: string | null; quantity: string }[];
    opened_packages: { id: string; packaging_quantity: string; remaining: string; opened_at: string; label: string }[];
  }[];
};

/** An inventory of chosen items of one warehouse (§ 11.6). */
export type Inventory = {
  id: string;
  number: number;
  version: number;
  state: "draft" | "submitted" | "confirmed" | "rejected";
  context: { id: string; location_name: string };
  comment: string;
  created_at: string;
  created_by: string;
  counted_by: string[];
  submitted_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string;
  lines: {
    id: string;
    kind: "asset" | "lot" | "found_unit" | "found_elsewhere" | "found_vehicle";
    item: ItemBrief | null;
    asset: string | null;
    lot: string | null;
    packaging_quantity: string | null;
    opened_package_id: string | null;
    book_quantity: string | null;
    counted_quantity: string | null;
    discrepancy: string | null;
    counted_at: string | null;
    description: string;
    identifiers: string;
    applied_delta: string | null;
    created_asset: string | null;
  }[];
};

export type PriceEntry = { packaging_id: string | null; amount: string; updated_at: string };

/** Selling prices and minimums of an item (§ 11.7.4–11.7.8). */
export type ItemPrices = {
  mode: "item" | "lot";
  currency: string;
  item_prices: PriceEntry[];
  lots: { id: string; public_id: string; prices: PriceEntry[] }[];
  assets: { id: string; public_id: string; sale_price: string | null }[];
  minimums: { context_id: string; location_name: string; quantity: string | null }[];
};

/** Service works (ТЗ гл. 16; decisions С-1…С-7). */
export type SystemWork = { code: string; names: Record<string, string> };

export type ServiceWork = {
  id: string;
  version: number;
  name: string;
  system_work: string | null;
  system_names: Record<string, string> | null;
  pricing: "fixed" | "hours";
  price: string | null;
  hours: string | null;
  rate: string | null;
  currency: string;
  state: "active" | "archived";
};

export type ServiceRate = { location_id: string | null; rate: string; currency: string };

export type ServicePost = {
  id: string;
  version: number;
  location_id: string;
  name: string;
  state: "active" | "archived";
};

export type ServiceJob = {
  id: string;
  version: number;
  operation_id: string;
  work_id: string;
  name: string;
  system_work: string | null;
  pricing: "fixed" | "hours";
  hours: string | null;
  rate: string | null;
  rate_source: "work" | "location" | "company" | null;
  price: string | null;
  currency: string;
  executor_id: string | null;
  executor_name: string | null;
  contractor_id: string | null;
  contractor_name: string | null;
  contractor_cost: string | null;
  state: "planned" | "in_progress" | "done" | "cancelled" | "terminated";
  actual_start: string | null;
  actual_end: string | null;
  odometer: string | null;
  comment: string;
  /** A finished work opened for editing (§ 16.8.2); it stays done meanwhile. */
  editing_since?: string | null;
  end_reason: string;
};

export type ServiceOperation = {
  id: string;
  version: number;
  number: number;
  state: "planned" | "accepted" | "in_progress" | "works_done" | "cancelled" | "terminated";
  order_id: string;
  order_number: number;
  order_state: string;
  is_internal: boolean;
  client_id: string | null;
  vehicle_card_id: string;
  vehicle_name: string | null;
  location_id: string | null;
  counterparty_id: string | null;
  counterparty_name: string | null;
  reason: string;
  planned_start: string | null;
  planned_end: string | null;
  post_id: string | null;
  post_name: string | null;
  accepted_at: string | null;
  finished_at: string | null;
  end_reason: string;
  created_at: string;
  jobs: ServiceJob[];
  materials: ServiceMaterial[];
  handovers: ServiceHandover[];
  blockers?: string[];
};

/** A part issued from a warehouse to a work (§ 16.5.1). */
export type ServiceMaterial = {
  id: string;
  version: number;
  job_id: string;
  item_name: string;
  label: string;
  unit: string;
  quantity: string;
  returned: string;
  consumed: string;
  outstanding: string;
  price: string | null;
  price_per: string;
  amount: string | null;
  currency: string;
};

export type QueueJob = ServiceJob & { operation_number: number; location_id: string; vehicle_name: string | null };

/** The vehicle or a part handed over to an external contractor (§ 16.6.6). */
export type ServiceHandover = {
  id: string;
  version: number;
  kind: "vehicle" | "part";
  description: string;
  counterparty_id: string;
  counterparty_name: string | null;
  handed_at: string;
  returned_at: string | null;
};

/** A periodic maintenance rule of a vehicle with its computed status (ТЗ гл. 9). */
export type MaintenanceRule = {
  id: string;
  version: number;
  vehicle_card_id: string;
  name: string;
  system_work: string | null;
  work_id: string | null;
  interval_distance: number | null;
  interval_days: number | null;
  remind_distance: number | null;
  remind_days: number | null;
  last_done_on: string | null;
  last_done_odometer: string | null;
  next_due_on: string | null;
  next_due_odometer: string | null;
  state: "active" | "archived";
  status: {
    state: "ok" | "soon" | "required" | "unknown";
    base: "work" | "manual_last" | "manual_next" | "none";
    base_on: string | null;
    base_odometer: string | null;
    due_on: string | null;
    due_odometer: string | null;
  };
};

/** A point of a move: a location of the company or «A / Other» (§ 15.1.2). */
export type DeliveryPoint = {
  kind: "location" | "other";
  location: string;
  name: string;
  address: string;
  comment: string;
  lat: string | null;
  lng: string | null;
};

/** A delivery or an internal move (ТЗ гл. 15). */
export type Delivery = {
  id: string;
  version: number;
  number: number;
  state: "planned" | "in_transit" | "completed" | "cancelled" | "terminated" | "annulled";
  order_id: string;
  order_number: number;
  is_internal: boolean;
  vehicle_card_id: string;
  vehicle_name: string | null;
  from: DeliveryPoint;
  to: DeliveryPoint;
  planned_start: string | null;
  planned_end: string | null;
  driver_id: string | null;
  driver_name: string | null;
  actual_start: string | null;
  actual_end: string | null;
  result: string | null;
  result_comment: string;
  end_reason: string;
  driver_overlaps?: { id: string; number: number; planned_start: string | null; planned_end: string | null }[];
  /** The paid position of a client delivery; not shown to a driver of their own trip. */
  service_id?: string | null;
  price?: string | null;
  currency?: string | null;
  expenses?: { id: string; number: number; amount: string; currency: string; article_id: string | null; note: string; posted_at: string | null }[];
  /** The current charge of the delivery and what began on the vehicle after it (§ 15.3.4–15.3.5). */
  charged?: string | null;
  later_operations?: { kind: string; id: string; number?: number; state?: string }[];
};

/** A delivery service of the catalog (§ 15.2.6). */
export type DeliveryService = {
  id: string;
  version: number;
  name: string;
  price: string;
  currency: string;
  state: "active" | "archived";
};

/** A kind of document: a starting one by its code or the company's own by its name (§ 18.1.11). */
export type DocumentKind = { id: string; code: string | null; name: string | null };

/** A document of the company registry (ТЗ гл. 18). */
export type CompanyDocument = {
  id: string;
  version: number;
  number: number;
  title: string;
  kind_id: string;
  document_date: string;
  all_locations: boolean;
  /** «Show to the client» in the personal cabinet (decision Б-2). */
  show_to_client: boolean;
  locations: string[];
  valid_until: string | null;
  warn_days: number | null;
  warning: "expiring" | "expired" | null;
  state: "active" | "archived";
  archive_reason: string;
  current_version: { number: number; file_id: string | null; file_name: string; has_content: boolean } | null;
  links?: { id: string; object_type: string; object_id: string }[];
  versions?: { number: number; file_id: string | null; file_name: string; valid_until: string | null; note: string; content: string; created_at: string }[];
  template_id: string | null;
  template_version: number | null;
  signing: { state: "none" | "pending" | "signed" | "declined"; version: number | null };
  source_type: string | null;
  source_id: string | null;
};

/** A signer of one version of a document (§ 18.3.3–18.3.7). */
export type DocumentSigner = {
  id: string;
  version_number: number;
  role: "company" | "client" | "counterparty";
  name: string;
  employment_id: string | null;
  client_id: string | null;
  counterparty_id: string | null;
  is_me: boolean;
  state: "pending" | "signed" | "declined";
  method: "in_app" | "link" | "paper" | "on_screen" | null;
  decided_at: string | null;
  confirmed_contact: string | null;
  comment: string;
  paper_file_id: string | null;
  paper_signed_on: string | null;
  entered_at: string | null;
  /** The image of a signature drawn on the screen (§ 10.2.2). */
  signature_file_id: string | null;
  presented_by_me: boolean;
  emails: string[];
  link: { email: string; expires_at: string } | null;
};

export type DocumentSigning = {
  document_version: number;
  status: CompanyDocument["signing"];
  methods: "both" | "in_app" | "paper";
  /** The client may sign on the screen of the employee's device: an act of inspection (§ 18.4.2). */
  on_screen: boolean;
  current_version: number | null;
  sent_version: number | null;
  template_signers: string[];
  signers: DocumentSigner[];
  candidates: { role: DocumentSigner["role"]; id: string; name: string }[];
};

/** What a personal link shows: only the assigned version (§ 18.3.10). */
export type SigningLink = {
  company: string;
  title: string;
  number: number;
  version_number: number;
  content: string;
  has_file: boolean;
  file_name: string;
  signer_name: string;
  email: string;
  expires_at: string;
};

/** A template: a starting one of the system or the company's own (§ 18.1.5, § 18.2). */
export type DocumentTemplate = {
  id: string;
  version: number;
  system: boolean;
  code: string | null;
  title: string;
  kind_id: string;
  source: "rental" | "order" | "service_operation" | "delivery" | "client" | "vehicle" | "inspection" | "none";
  language: string;
  content: string;
  required_fields: string[];
  signers: Record<string, boolean>;
  state: "active" | "archived";
  template_version: number;
};

/** A work task of the company (§ 19.1, § 23.9). */
export type WorkTask = {
  id: string;
  version: number;
  number: number;
  title: string;
  priority: "low" | "normal" | "high" | "critical";
  state: "open" | "in_progress" | "waiting" | "paused" | "done" | "cancelled";
  state_note: string;
  location_id: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  is_mine: boolean;
  source: "manual" | "system";
  object_type: string | null;
  object_id: string | null;
  due_at: string | null;
  due_has_time: boolean;
  overdue: boolean;
  created_at: string;
  closed_at: string | null;
  blocked: boolean;
  group_id: string | null;
  tags: { id: string; name: string }[];
  repeating: boolean;
  // Detail only.
  reminders: number[];
  checklist_required: boolean;
  checklist: { id: string; text: string; done: boolean; done_at: string | null }[];
  predecessors: { id: string; number: number; title: string; state: string }[];
  series: {
    id: string;
    version: number;
    frequency: "daily" | "weekly" | "monthly";
    weekdays: number[];
    ends_on: string | null;
    next_on: string | null;
    state: "active" | "paused" | "stopped";
  } | null;
  series_on: string | null;
  description: string;
  system_event: string | null;
  system_condition: string;
  cancel_reason: string;
  created_by_name: string | null;
  is_creator: boolean;
  links: { id: string; object_type: string; object_id: string }[];
  comments: { id: string; text: string; file_id: string | null; file_name: string; author: string; created_at: string }[];
  history: { id: string; occurred_at: string; actor: { name: string }; action: string; reason: string }[];
  can: { manage: boolean; own: boolean; queue: boolean };
  policy: { executor_may_release: boolean; executor_may_change_due: boolean; overdue_repeat_hours: number; version: number };
};

// Block 8 (ТЗ гл. 10): inspections, templates, policies and damages.
export type InspectionKind = "issue" | "return" | "out_of_rental" | "intermediate";
export type InspectionApplication = "rental" | "service" | "standalone";
export type InspectionState =
  | "draft"
  | "planned"
  | "in_progress"
  | "saved"
  | "cancelled"
  | "terminated"
  | "invalid";

export type InspectionFileItem = {
  file_id: string;
  kind: "photo" | "video";
  position: string | null;
  /** A video is compressed on the server after its upload (decision В-8.1). */
  state?: "ready" | "processing" | "failed";
};

export type TemplatePosition = {
  code: string;
  name?: string;
  enabled: boolean;
  required: boolean;
  count: number;
};

export type DamageEstimate = { amount: string; currency: string };

export type DamageDraftEntry = {
  zone_scheme?: string;
  zone_x?: string | null;
  zone_y?: string | null;
  zone_text?: string;
  description?: string;
  photo_file_ids: string[];
  estimate?: DamageEstimate | null;
  attributed_to_rental?: boolean;
  returns_damage_id?: string | null;
};

export type DamageEvent = {
  id: string;
  kind: "created" | "estimated" | "fixed" | "returned" | "erroneous";
  state_before: string | null;
  state_after: string;
  occurred_at: string;
  odometer: string | null;
  basis_type: "inspection" | "service_job" | null;
  basis_id: string | null;
  estimate: DamageEstimate | null;
  comment: string;
  created_by_user_id: string;
  created_at: string;
  files: { file_id: string; role: "selected" | "fix_result" }[];
};

export type Damage = {
  id: string;
  vehicle_card_id: string;
  inspection_id: string;
  rental_id: string | null;
  attributed_to_rental: boolean;
  performed_at: string;
  odometer: string | null;
  odometer_unit: string;
  zone_scheme: string | null;
  zone_x: string | null;
  zone_y: string | null;
  zone_text: string;
  description: string;
  state: "actual" | "fixed" | "erroneous";
  estimate: DamageEstimate | null;
  charged: string | null;
  version: number;
  history?: DamageEvent[];
};

export type Inspection = {
  id: string;
  /** The local draft it was sent from without connection (§ 10.4.3); null — made online. */
  client_draft_id?: string | null;
  vehicle_card_id: string;
  kind: InspectionKind;
  application: InspectionApplication;
  rental_id: string | null;
  service_operation_id: string | null;
  origin: "rental" | "form";
  state: InspectionState;
  check_status: "conducted" | "not_required" | "required_not_done";
  check_required: boolean;
  performed_at: string;
  planned_at: string | null;
  started_at: string | null;
  saved_at: string | null;
  place: {
    kind: "location" | "other";
    location_id: string | null;
    note: string;
    address: string;
    lat: string | null;
    lng: string | null;
  };
  participant: {
    kind: "client" | "counterparty" | "employee" | "other" | null;
    client_id: string | null;
    counterparty_id: string | null;
    employment_id: string | null;
    text: string;
  };
  performer_employment_id: string;
  author_user_id: string;
  entered_at: string;
  odometer: string | null;
  odometer_unit: string;
  fuel: string | null;
  cleanliness: string;
  end_reason: string;
  ended_at: string | null;
  invalidated_reason: string;
  invalidated_at: string | null;
  version: number;
  files: InspectionFileItem[];
  // Detail only.
  template?: { id: string; template_id: string; number: number; positions: TemplatePosition[] } | null;
  damages_draft?: DamageDraftEntry[];
  damages?: Damage[];
};

export type InspectionTemplate = {
  id: string;
  transport: "car" | "motorcycle";
  version: number;
  version_id: string;
  positions: TemplatePosition[];
  base_positions: string[];
  updated_at: string;
};

export type InspectionPolicy = {
  kind: InspectionKind;
  application: InspectionApplication;
  // Null for an issue and a return of a rental: set by the issue policy (§ 14.3.6).
  check_required: boolean | null;
  odometer_required: boolean;
  fuel_required: boolean;
  cleanliness_required: boolean;
  version: number;
};
