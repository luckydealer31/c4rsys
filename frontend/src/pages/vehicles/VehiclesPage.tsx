import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { RentalSummary, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon } from "../../components/icons";
import { Card, ErrorMessage, Field, IconButton, Info, Loading } from "../../components/ui";
import { VehicleTypeIcon } from "../../components/VehicleTypeIcon";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { CompanyTabs, workHome } from "../../layout/CompanyTabs";
import { SectionTabs } from "../../layout/SectionTabs";
import { VehicleInspectionsSplit, VehicleRentalsSplit } from "./VehiclePeek";
import { VehicleTariffTerms } from "./VehicleTariffTerms";
import { useFirstTab } from "../../layout/tabOrder";
import { useLocations } from "../settings/LocationsPage";
import { SectionSearch } from "../search/SearchPage";
import { VehicleFinance } from "./VehicleFinance";
import { VehicleJournal } from "./VehicleJournal";
import { VehicleService } from "../service/VehicleService";
import { VehicleParts } from "../warehouse/Reserves";
import { OwnerRequestCard } from "./OwnerRequestCard";
import { VehicleDocumentsList, VehicleDocumentsSettings } from "./VehicleDocuments";
import { DraftsPanel } from "../inspections/OfflineDrafts";
import { VehiclePhotos } from "./VehiclePhotos";
import { RowTags, TagsLine, useTagsOf } from "../tags/Tags";
import { useScope } from "../../offline/hooks";
import { VehicleMoves } from "./VehicleMoves";
import { VehicleDamagesCard } from "../inspections/Blocks";
import {
  ConflictsCard,
  DuplicatesHistory,
  MergeCard,
  MergesCard,
  OperationalDisputeBanner,
  OwnerDataBanner,
  PendingMergeBanner,
  ProposalsCard,
} from "./VehicleIdentity";
import {
  DisclosureCard,
  DisposeCard,
  GlobalDocumentsCard,
  GlobalDocumentsList,
  HistoryCard,
  OdometerCard,
  RelationCard,
  RestrictionCard,
  ReversalCard,
} from "./VehicleLifecycle";

// Only operational contexts are switched for a vehicle — rental now (decision of 24.10.2026):
// service is implied, the warehouse is reached by a transfer of an own vehicle (§ 8.1.5).
const USAGE_CONTEXTS = ["rental"] as const;

export function useVehicles() {
  const company = useCompany();
  return useApi<{ vehicles: Vehicle[] }>(["vehicles", company.public_id], `/c/${company.public_id}/vehicles`);
}

export function useRentals(enabled = true) {
  const company = useCompany();
  return useApi<{ rentals: RentalSummary[] }>(
    ["rentals", company.public_id],
    `/c/${company.public_id}/rentals`,
    enabled,
  );
}

type CardTab =
  | "main"
  | "service"
  | "inspections"
  | "finance"
  | "rentals"
  | "moves"
  | "ownership"
  | "settings";
// The parts of the history of a vehicle: one tab instead of four (decision of 13.11.2026).
type HistoryPart = "rentals" | "moves" | "finance" | "ownership";
// The settings of the card are split into tabs under the tab (customer's decision of 24.11.2026).
type SettingsPart = "general" | "tariffs" | "documents" | "access" | "card" | "history";
const SETTINGS_PARTS: SettingsPart[] = ["general", "tariffs", "documents", "access", "card", "history"];

/** Garage (§ 8.7.2; customer's decisions of 24.10.2026 and 11.11.2026). Own and managed vehicles
 * are one list, grouped by location, with the switches «own» / «managed»; the schedule is the
 * second view of the same vehicles with the same switches. The list is switched between the
 * fleet and the client vehicles, which are shown alone, without locations and without the
 * switches of the fleet; the schedule has no client vehicles.
 * The actions of the section stand in a column on the right. */
type Relation = "own" | "managed" | "client";
type View = "list" | "client";
// Any right of the kind opens the view, as the items of the menu did before.
const FLEET: ("own" | "managed")[] = ["own", "managed"];

export function VehiclesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const scope = useScope();
  // § 2.8.3: by the c4rsys ID, a part of the name, the VIN and the plate.
  const q = params.get("q") ?? "";
  const all = useVehicles();
  const found = useApi<{ vehicles: Vehicle[] }>(
    ["vehicles", company.public_id, "q", q],
    `/c/${company.public_id}/vehicles?q=${encodeURIComponent(q)}`,
    Boolean(q),
  );
  // § 21.3.1: the list a block of the company overview is counted from.
  const only = ["unavailable", "identifier_conflict"].includes(params.get("only") ?? "") ? (params.get("only") ?? "") : "";
  const filtered = useApi<{ vehicles: Vehicle[] }>(
    ["vehicles", company.public_id, "only", only],
    `/c/${company.public_id}/vehicles?only=${only}`,
    Boolean(only),
  );
  const vehicles = only ? filtered : q ? found : all;
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const tagsOf = useTagsOf("vehicle");
  // Links of the earlier tabs (?tab=managed, ?tab=client) keep working.
  const tab = params.get("tab");
  const wanted = params.get("view");
  // Trips are a view of the garage, with their rights (decision of 11.11.2026). Inspections
  // have no list of their own: they are found in the vehicle, the rental or the service.
  const view: View = tab === "client" ? "client" : "list";
  const rel = params.get("rel") ?? (tab === "managed" || tab === "own" ? tab : "");
  const shown = new Set<Relation>(view === "client" ? ["client"] : rel === "own" || rel === "managed" ? [rel] : FLEET);
  // The cards in the archive are not shown until asked for, and then only they are (customer's
  // decision of 24.11.2026).
  const archive = params.get("archive") === "1";
  const go = (changes: { view?: View; rel?: string; q?: string; archive?: boolean }) => {
    const next = { view, rel, q, archive, ...changes };
    setParams(
      Object.fromEntries(
        Object.entries({
          tab: next.view === "client" ? "client" : "",
          rel: next.view === "client" ? "" : next.rel,
          q: next.q,
          archive: next.archive ? "1" : "",
        }).filter(([, value]) => value),
      ),
    );
  };
  // Both switches are on at first; the last one that is on stays on.
  const toggle = (key: "own" | "managed") => {
    if (shown.has(key) && shown.size === 1) return;
    go({ rel: shown.has(key) ? (key === "own" ? "managed" : "own") : "" });
  };
  // Own and managed vehicles are grouped by their location, in the order of the locations;
  // those without a location come last. Client vehicles have no location (§ 8.1.2).
  // Vehicles on a warehouse are stock items, shown in «Warehouse» (§ 8.1.5).
  const list = (vehicles.data?.vehicles ?? []).filter(
    (v) =>
      (only ? true : shown.has(v.relation as Relation)) &&
      v.storage === null &&
      (v.state === "archived") === archive,
  );
  const groups = (view === "client"
    ? [{ id: "client", name: "", vehicles: list }]
    : [
    ...(locations.data?.locations ?? []).map((l) => ({
      id: l.id,
      name: l.name,
      vehicles: list.filter((v) => v.location_id === l.id),
    })),
    {
      id: "none",
      name: t("vehicles.no_location"),
      vehicles: list.filter(
        (v) => !v.location_id || !locations.data?.locations.some((l) => l.id === v.location_id),
      ),
    },
  ]).filter((group) => group.vehicles.length > 0);
  const busy = new Set((rentals.data?.rentals ?? []).filter((r) => r.state === "active").map((r) => r.vehicle_card_id));
  // A new vehicle starts with the relation of the list it is created from.
  const newRelation = view === "client" ? "client" : shown.size === 1 && shown.has("managed") ? "managed" : "own";
  const actions: [boolean, string, string][] =
    view === "client"
      ? [
          [can("vehicle.create"), `/vehicles/new?relation=client`, t("vehicles.create_button")],
        ]
      : [
          [can("vehicle.create"), `/vehicles/new?relation=${newRelation}`, t("vehicles.create_button")],
        ];
  // A rental, a booking, a service operation and a move are created in their own sections and
  // in the card of the vehicle, not from the list of the garage (decision of 14.11.2026).
  const allowed = actions.filter(([may]) => may);
  // The schedule and the trips moved to «Rental» (decision of 14.11.2026); old addresses lead there.
  if (wanted === "schedule" || wanted === "trips") {
    return <Navigate to={companyPath(company, `${workHome(company)}?tab=${wanted}`)} replace />;
  }
  return (
    <div className="page full">
      <h1 className="sr-only">{t("nav.garage")}</h1>
      <CompanyTabs current="garage" />
      {/* The fleet and the client vehicles are two lists that never mix: tabs under the tabs of
          the section, put in order by the same button (customer's decision of 24.11.2026). The
          switches of one list — «own», «managed», «archive» — stay switches. */}
      <SectionTabs
        sub
        section="garage_lists"
        label={t("vehicles.scope_title")}
        current={view}
        tabs={[
          { key: "list" as View, text: t("vehicles.scope_fleet") },
          { key: "client" as View, text: t("vehicles.tab_client") },
        ]}
        onSelect={(key) => go(key === "client" ? { view: "client", rel: "" } : { view: "list" })}
      />
      <div className={allowed.length ? "split" : ""}>
        <div>
          <div className="list-bar">
            {/* The list is either the fleet or the client vehicles; the two never mix. The
                switches «own» / «managed» belong to the fleet only. */}
            {view === "client"
              ? null
              : FLEET.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className="toggle-chip"
                    aria-pressed={shown.has(key)}
                    onClick={() => toggle(key)}
                  >
                    {t(`vehicles.tab_${key}`)}
                  </button>
                ))}
            <button type="button" className="toggle-chip" aria-pressed={archive} onClick={() => go({ archive: !archive })}>
              {t("vehicles.archive_switch")}
            </button>
            <SectionSearch compact value={q} onSearch={(text) => go({ q: text })} />
          </div>
          {only ? (
            <p className="small muted">
              {t(`vehicles.only_${only}`)} ·{" "}
              <Link to={companyPath(company, "/vehicles")}>{t("vehicles.only_reset")}</Link>
            </p>
          ) : null}
          {(
            <>
              {/* Inspections started on this device and not sent yet (§ 10.4.2–10.4.4): shown
                  only while there are any, so that none is lost. */}
              <DraftsPanel
                scope={scope}
                base={companyPath(company)}
                zone={company.timezone ?? "UTC"}
                canCreate={can("inspection.create")}
              />
              {vehicles.isLoading ? <Loading /> : null}
              {vehicles.data && list.length === 0 ? (
                <Card>
                  <p className="muted">{t("vehicles.none")}</p>
                </Card>
              ) : null}
              {groups.map((group) => (
                <Card key={group.id} title={group.name}>
                  <div className="table-scroll">
                    {/* The columns are of one width in every location, so they stand in one
                        vertical. */}
                    <table className="fleet-table">
                      <colgroup>
                        <col />
                        {view === "client" ? null : <col className="col-state" />}
                        <col className="col-odometer" />
                      </colgroup>
                      <thead>
                        <tr>
                          <th>{t("vehicles.name")}</th>
                          {view === "client" ? null : <th>{t("vehicles.occupancy")}</th>}
                          <th>{t("vehicles.odometer")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.vehicles.map((vehicle) => (
                          <tr key={vehicle.id}>
                            <td>
                              <div className="vehicle-cell">
                                <VehicleTypeIcon
                                  type={vehicle.characteristics.vehicle_type}
                                  body={vehicle.characteristics.body_type}
                                />
                                <div>
                                  <Link to={companyPath(company, `/vehicles/${vehicle.id}`)}>{vehicle.name}</Link>{" "}
                                  {/* One list for own and managed: the managed ones are marked. */}
                                  {view !== "client" && vehicle.relation === "managed" ? (
                                    <span className="badge">{t("vehicles.relation_managed")}</span>
                                  ) : null}{" "}
                                  <RowTags tags={tagsOf(vehicle.id)} />
                                  <div className="muted small">{vehicle.vehicle_id}</div>
                                </div>
                              </div>
                            </td>
                            {view === "client" ? null : (
                              <td data-label={t("vehicles.occupancy")}>
                                {busy.has(vehicle.id) ? (
                                  <span className="badge warn">{t("vehicles.in_rental")}</span>
                                ) : (
                                  <span className="badge ok">{t("vehicles.free")}</span>
                                )}
                              </td>
                            )}
                            <td data-label={t("vehicles.odometer")}>
                              {vehicle.odometer ? `${Number(vehicle.odometer.value)} ${vehicle.odometer.unit}` : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              ))}
            </>
          )}
        </div>
        {allowed.length ? (
          <aside className="side-actions" aria-label={t("vehicles.actions_title")}>
            {allowed.map(([, path, label]) => (
              <Link key={path} className="button secondary" to={companyPath(company, path)}>
                <Icon name="plus" size={16} /> {label}
              </Link>
            ))}
          </aside>
        ) : null}
      </div>
    </div>
  );
}

// The main data of the vehicle in the order the customer named on 12.11.2026: what does not
// change (the colour aside). The rest of the characteristics follow in the second column.
const MAIN = [
  "vehicle_type", "make", "model", "year", "body_type", "color", "vin", "body_number", "chassis_number",
  "frame_number", "engine_number", "engine_volume_cc", "power_hp", "fuel_type", "tank_volume_l", "battery_kwh",
];

/** Identification data and characteristics of the vehicle; a coincidence of VIN or plate with
 * another vehicle is shown as a reason to check (§ 8.6.3). */
function VehicleFacts({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const c = vehicle.characteristics;
  const option = (prefix: string, value: string) => (value ? t(`${prefix}.${value}`) : "");
  const can = useCan();
  const company = useCompany();
  const owner = vehicle.owner;
  // Not given is said plainly (decision of 24.10.2026).
  const ownerText = !owner
    ? vehicle.relation === "own"
      ? company.display_name
      : t("vehicles.owner_not_given")
    : owner.kind === "company"
      ? (owner.company?.display_name ?? "")
      : owner.kind === "client"
        ? (owner.client?.name ?? "")
      : owner.kind === "profile"
        ? owner.lookup
        : owner.kind === "none"
          ? t("vehicles.owner_not_given")
          : [owner.name, owner.contact, owner.address].filter(Boolean).join(", ");
  const working = vehicle.working_values ?? {};
  const confirmed = new Set(vehicle.confirmed_fields ?? []);
  const facts: [string, string, string?][] = [
    [
      t("vehicles.section_owner"),
      // Only the owner of a managed vehicle is asked to confirm (§ 8.3.1, § 8.3.13).
      owner?.source === "ownership_change"
        ? `${t("vehicles.owner_new_after_change")}${owner.confirmed ? "" : ` (${t("vehicles.owner_unconfirmed")})`}`
        : vehicle.relation === "managed" && owner && owner.kind !== "none" && !owner.confirmed
          ? `${ownerText} (${t("vehicles.owner_unconfirmed")})`
          : ownerText,
    ],
    [t("vehicles.vehicle_type"), c.vehicle_type ? t(`vehicles.type_${c.vehicle_type}`) : "", "vehicle_type"],
    [t("vehicles.make"), c.make_name, "make"],
    [t("vehicles.model"), c.model_name, "model"],
    [t("vehicles.year"), c.year ? String(c.year) : "", "year"],
    [t("vehicles.odometer"), vehicle.odometer ? `${Number(vehicle.odometer.value)} ${vehicle.odometer.unit}` : "", "#odometer"],
    [t("vehicles.color"), c.color, "color"],
    // Only the number: the country of registration is kept but not shown (decision of 12.11.2026).
    [t("vehicles.plate"), c.plate, "#plate"],
    [t("vehicles.plate_region"), c.plate_region ?? ""],
    [t("vehicles.vin"), c.vin, "vin"],
    [t("vehicles.body_number"), c.body_number, "body_number"],
    [t("vehicles.chassis_number"), c.chassis_number, "chassis_number"],
    [t("vehicles.frame_number"), c.frame_number, "frame_number"],
    [t("vehicles.engine_number"), c.engine_number, "engine_number"],
    [t("vehicles.body_type"), option("vehicles.body", c.body_type), "body_type"],
    [t("vehicles.fuel_type"), option("vehicles.fuel", c.fuel_type), "fuel_type"],
    [t("vehicles.transmission"), option("vehicles.gearbox", c.transmission), "transmission"],
    [t("vehicles.drive"), option("vehicles.drives", c.drive), "drive"],
    [t("vehicles.engine_volume_cc"), c.engine_volume_cc ? String(c.engine_volume_cc) : "", "engine_volume_cc"],
    [t("vehicles.power_hp"), c.power_hp ? String(c.power_hp) : "", "power_hp"],
    [t("vehicles.seats"), c.seats ? String(c.seats) : "", "seats"],
    [t("vehicles.doors"), c.doors !== null ? String(c.doors) : "", "doors"],
    [t("vehicles.manufacturer"), c.manufacturer ?? "", "manufacturer"],
    [t("vehicles.manufacture_country"), c.manufacture_country ?? "", "manufacture_country"],
    [t("vehicles.modification"), c.modification ?? "", "modification"],
    [t("vehicles.tank_volume_l"), c.tank_volume_l ?? "", "tank_volume_l"],
    [t("vehicles.battery_kwh"), c.battery_kwh ?? "", "battery_kwh"],
    [t("vehicles.range_km"), c.range_km ? String(c.range_km) : "", "range_km"],
    [t("vehicles.gears"), c.gears ? String(c.gears) : "", "gears"],
    [t("vehicles.curb_weight_kg"), c.curb_weight_kg ? String(c.curb_weight_kg) : "", "curb_weight_kg"],
    [t("vehicles.gross_weight_kg"), c.gross_weight_kg ? String(c.gross_weight_kg) : "", "gross_weight_kg"],
    [t("vehicles.payload_kg"), c.payload_kg ? String(c.payload_kg) : "", "payload_kg"],
    [t("vehicles.cargo_volume_m3"), c.cargo_volume_m3 ?? "", "cargo_volume_m3"],
    [t("vehicles.power_kw"), c.power_kw ?? "", "power_kw"],
    [t("vehicles.vehicle_category"), c.vehicle_category ?? "", "vehicle_category"],
    [t("vehicles.eco_class"), c.eco_class ?? "", "eco_class"],
    [t("vehicles.type_approval"), c.type_approval ?? "", "type_approval"],
    [t("vehicles.special_marks"), c.special_marks ?? "", "special_marks"],
    [t("vehicles.inventory_number"), c.inventory_number ?? ""],
    [t("vehicles.barcode"), c.barcode ?? ""],
  ];
  // A value chosen from a list is named, not shown by its code.
  const lists: Record<string, string> = {
    body_type: "vehicles.body",
    fuel_type: "vehicles.fuel",
    transmission: "vehicles.gearbox",
    drive: "vehicles.drives",
  };
  const named = (field: string, display: string) =>
    !display
      ? "—"
      : field === "vehicle_type"
        ? t(`vehicles.type_${display}`)
        : lists[field]
          ? option(lists[field], display)
          : display;
  const renderFacts = (column: [string, string, string?][]) => (
            <dl className="facts">
              {column
                .filter(([, value, field]) => value || (field && working[field]))
                .map(([label, value, field]) => {
            // § 8.2.1: the card's own working value is shown with the mark of the discrepancy.
            const own = field ? working[field] : undefined;
            return (
              <div key={label} style={{ display: "contents" }}>
                <dt>{label}</dt>
                <dd>
                  {own ? (
                    <>
                      {named(field ?? "", own.display)}{" "}
                      <span className="badge warn">
                        {t(own.owner_newer ? "vehicles.owner_newer_mark" : "vehicles.working_value_mark", {
                          value: named(field ?? "", own.global.display),
                        })}
                      </span>
                    </>
                  ) : (
                    value
                  )}
                  {field && confirmed.has(field) ? (
                    <span className="badge ok" style={{ marginLeft: 6 }}>
                      {t("vehicles.confirmed_mark")}
                    </span>
                  ) : null}
                </dd>
              </div>
            );
          })}
            </dl>
  );
  return (
    <Card>
      {/* The tab is named «Main» already, so the block has no title of its own; the pencil
          stands in its right corner (customer's remark of 24.11.2026). */}
      {can("vehicle.edit") && vehicle.state !== "archived" ? (
        <div className="corner-action">
          <IconButton
            icon="edit"
            label={t("common.edit")}
            onClick={() => navigate(companyPath(company, `/vehicles/${vehicle.id}/edit`))}
          />
        </div>
      ) : null}
      {c.identifier_matches.length ? (
        <div className="alert warn">
          {c.identifier_matches.map((m) => t(`vehicles.match_${m}`)).join(" ")}
        </div>
      ) : null}
      {/* Two columns (decisions of 12.11.2026): the characteristics on the left; on the right the
          plate and the owner, and under them the documents — the documents of a vehicle follow
          its plate. Documents are added and changed in the settings of the card. */}
      <div className="facts-columns">
        {renderFacts([
          ...MAIN.flatMap((key) => facts.filter(([, , field]) => field === key)),
          ...facts.filter(([, , field]) => field === "#odometer"),
          ...facts.filter(([, , field]) => field && !field.startsWith("#") && !MAIN.includes(field)),
        ])}
        <div>
          {/* The photos stand above the plate: the documents under it may be many (decision of
              17.11.2026). */}
          <VehiclePhotos vehicle={vehicle} />
          {/* The owner, then the registration number and its region — the order of the tab
              «Ownership» (customer's remark of 24.11.2026); then the labels of the card. */}
          {renderFacts([
            ...facts.filter(([label]) => label === t("vehicles.section_owner")),
            ...facts.filter(([, , field]) => field === "#plate"),
            ...facts.filter(([label, , field]) => !field && label !== t("vehicles.section_owner")),
          ])}
          <h3 style={{ marginTop: 20 }}>{t("nav.documents")}</h3>
          <GlobalDocumentsList vehicle={vehicle} />
          <VehicleDocumentsList vehicle={vehicle} />
        </div>
      </div>
      {vehicle.changes_global === false ? (
        <p className="muted small">{t("vehicles.not_owner_hint")}</p>
      ) : null}
    </Card>
  );
}

/** Vehicle card: name, relation, accounting location, odometer unit, allowed contexts, tariffs,
 * rentals (§ 8.1, § 8.5, § 8.7.7). */
export function VehiclePage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const firstTab = useFirstTab("vehicle_card");
  const firstSettings = useFirstTab("vehicle_settings");
  const navigate = useNavigate();
  const route = useLocation();
  const draftScope = useScope();
  const vehicle = useApi<{ vehicle: Vehicle }>(["vehicle", company.public_id, id], `/c/${company.public_id}/vehicles/${id}`);
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/vehicles/${id}`, "PATCH");
  if (vehicle.isLoading) return <div className="page"><Loading /></div>;
  const data = vehicle.data?.vehicle;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;
  // § 8.6.6: a merged duplicate redirects to the main card.
  if (data.state === "merged" && data.merged_into) {
    return <Navigate to={companyPath(company, `/vehicles/${data.merged_into.id}`)} replace />;
  }
  const location = locations.data?.locations.find((l) => l.id === data.location_id);
  const timeZone = location?.timezone ?? company.timezone ?? "UTC";
  // The rentals of the duplicates merged into this card are shown here too (§ 8.6.6).
  const family = new Set([data.id, ...(data.merged_cards ?? []).map((m) => m.id)]);
  // Rentals and bookings together, the latest first (customer's remark of 24.11.2026).
  const startOf = (r: { actual_start: string | null; booking?: { planned_start: string } | null }) =>
    r.actual_start ?? r.booking?.planned_start ?? null;
  const own = (rentals.data?.rentals ?? [])
    .filter((r) => family.has(r.vehicle_card_id))
    .sort((a, b) => (startOf(b) ?? "").localeCompare(startOf(a) ?? ""));
  // Rental, moves, tariffs and contexts concern only own and managed vehicles in the garage
  // (§ 8.1.2, § 8.1.5).
  const stored = data.storage !== null;
  const isClient = data.relation === "client" || stored;

  const current = data;
  async function save(changes: Record<string, unknown>) {
    if ((await update.run({ ...changes, expected_version: current.version })) !== null) {
      await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, id] });
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  function toggleContext(type: string) {
    const next = current.contexts.includes(type)
      ? current.contexts.filter((c) => c !== type)
      : [...current.contexts, type];
    void save({ contexts: next });
  }

  // The card is split into tabs instead of one long sheet (customer's decision of 11.11.2026).
  // A vehicle in a warehouse or of a client has fewer of them (§ 8.1.2, § 8.1.5).
  // Finance, rentals, moves and ownership are tabs of their own; «History» holds the changes of
  // the card only (customer's decision of 24.11.2026, instead of the one tab «History» of
  // 13.11.2026). There is no tab of tasks.
  const tabs: CardTab[] = [
    "main",
    ...(stored ? [] : (["service", "inspections"] as CardTab[])),
    ...(isClient ? [] : (["finance", "rentals", "moves"] as CardTab[])),
    ...(stored && data.state === "active" ? (["moves"] as CardTab[]) : []),
    "ownership",
    "settings",
  ];
  // Old addresses: damages are shown with the inspections, documents on the main tab; the
  // former parts of the history are tabs now.
  const old: Record<string, string> = { damages: "inspections", documents: "main", tasks: "main" };
  const named = search.get("tab") ?? "";
  const formerPart = named === "history" ? (search.get("part") ?? "") : "";
  // The merge of a duplicate is an action: it stands in the settings. The journal of changes —
  // the former tab «History» — is a part of the settings too.
  const asked = search.get("merge")
    ? "settings"
    : named === "history"
      ? formerPart && formerPart !== "changes"
        ? formerPart
        : "settings"
      : (old[named] ?? named);
  const tab: CardTab = tabs.find((key) => key === asked) ?? firstTab(tabs) ?? "main";
  // What the blocks below are shown by.
  const part: HistoryPart | null =
    tab === "finance" || tab === "rentals" || tab === "moves" || tab === "ownership" ? tab : null;
  const askedSettings = search.get("merge") ? "card" : named === "history" ? "history" : search.get("part");
  // A client vehicle is only serviced: it has no tariffs.
  const settingsParts = SETTINGS_PARTS.filter((key) => key !== "tariffs" || !isClient);
  const settingsPart: SettingsPart =
    settingsParts.find((key) => key === askedSettings) ?? firstSettings(settingsParts) ?? "general";
  const openTab = (key: CardTab) => setSearch({ tab: key }, { replace: true });
  const openSettings = (key: SettingsPart) => setSearch({ tab: "settings", part: key }, { replace: true });

  return (
    <div className="page wide">
      {/* One line (decision of 12.11.2026): the name, the ID of the vehicle in the system and its
          relation to the company — so that the tabs stand higher. */}
      <div className="card-head">
        {/* Back to where the card was opened from — the list, the schedule, a trip (decision of
            14.11.2026). The tabs of the card replace the address, so one step back leaves the
            card. Opened by a direct address, the card leads back to the list. */}
        <IconButton
          icon="back"
          label={route.key !== "default" ? t("common.back") : t("nav.garage")}
          onClick={() => (route.key !== "default" ? navigate(-1) : navigate(companyPath(company, "/vehicles")))}
        />
        <h1>{data.name}</h1>
        <span className="muted">{data.vehicle_id}</span>
        <span className="badge">{t(`vehicles.relation_${data.relation}`)}</span>
        <TagsLine kind="vehicle" id={data.id} />
      </div>
      {stored ? (
        <div className="alert warn">
          {t("vehicles.in_warehouse", {
            location: locations.data?.locations.find((l) => l.id === data.storage?.location_id)?.name ?? "—",
          })}
        </div>
      ) : null}
      <PendingMergeBanner vehicle={data} />
      <OperationalDisputeBanner vehicle={data} />
      <OwnerDataBanner vehicle={data} />
      {data.state === "archived" ? (
        <div className="alert">{t("vehicles.archived_read_only")}</div>
      ) : null}
      {data.restriction?.state === "active" ? (
        <div className="alert warn">{t("vehicles.restricted_banner")}</div>
      ) : null}
      <SectionTabs
        section="vehicle_card"
        current={tab}
        tabs={tabs.map((key) => ({ key, text: t(`vehicles.card_${key}`) }))}
        onSelect={openTab}
      />
      {tab === "main" ? (
      <>
      <VehicleFacts vehicle={data} />
      {/* Only the conflicts being checked now; the closed ones are history (customer's remark of
          24.11.2026). */}
      <ConflictsCard vehicle={data} only="open" />
      <ProposalsCard vehicle={data} />
      </>
      ) : null}
      {/* Name, contexts of use and the odometer with its unit (decision of 11.11.2026: these are
          settings, not characteristics). */}
      {tab === "settings" ? (
        <>
          <SectionTabs
            sub
            section="vehicle_settings"
            current={settingsPart}
            tabs={settingsParts.map((key) => ({ key, text: t(`vehicles.settings_${key}`) }))}
            onSelect={openSettings}
          />
          {settingsPart === "general" ? (
            <>
              {/* What the vehicle is used in, with the readiness beside the mark it belongs to,
                  and the restriction — one block (customer's remark of 24.11.2026). The name is
                  not repeated: it stands in the head of the card and is changed by the pencil
                  of the main tab. A client vehicle is only serviced: nothing to allow (decision
                  of 24.10.2026). */}
              {isClient ? null : (
                <Card>
                  <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {t("vehicles.contexts")} <Info text={t("vehicles.contexts_hint")} />
                  </h3>
                  {USAGE_CONTEXTS.map((type) => (
                    <div key={type} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <label>
                        <input
                          type="checkbox"
                          disabled={!can("vehicle.edit")}
                          checked={data.contexts.includes(type)}
                          onChange={() => toggleContext(type)}
                        />{" "}
                        {t(`contexts.${type}`)}
                      </label>
                      {type === "rental" && data.contexts.includes(type) ? (
                        data.rental_readiness.ready ? (
                          <span className="badge ok">{t("setup.ready")}</span>
                        ) : (
                          <span className="badge warn">
                            {data.rental_readiness.missing.map((m) => t(`vehicles.missing_${m}`)).join("; ")}
                          </span>
                        )
                      ) : null}
                    </div>
                  ))}
                  <ErrorMessage error={update.error} />
                  <div style={{ marginTop: 16 }}>
                    <RestrictionCard vehicle={data} bare />
                  </div>
                </Card>
              )}
              {isClient ? <RestrictionCard vehicle={data} /> : null}
              <OdometerCard vehicle={data} />
            </>
          ) : null}
          {/* The tariffs of the vehicle with what they hold for it, changed here (customer's
              remark of 24.11.2026). */}
          {settingsPart === "tariffs" ? <VehicleTariffTerms vehicleId={data.id} /> : null}
          {settingsPart === "documents" ? (
            <>
              {/* Documents of the vehicle are added and changed here (decision of 12.11.2026);
                  making a document by a template is not offered in the card of a vehicle. */}
              <VehicleDocumentsSettings vehicle={data} />
              {/* The owner's documents and the company's copies of them (§ 8.2.3, § 8.2.7) — only
                  when there are any. */}
              {data.global_documents?.documents.length || data.global_documents?.copies.length ? (
                <GlobalDocumentsCard vehicle={data} />
              ) : null}
            </>
          ) : null}
          {settingsPart === "access" ? (
            <>
              {data.relation === "managed" ? <OwnerRequestCard vehicle={data} /> : null}
              <DisclosureCard vehicle={data} />
            </>
          ) : null}
          {/* To end the management, to archive, to restore, to merge and to split are actions with
              the card itself (customer's remarks of 24.11.2026). */}
          {settingsPart === "card" ? (
            <>
              <RelationCard vehicle={data} />
              <DisposeCard vehicle={data} />
              <ReversalCard vehicle={data} />
              <MergeCard key={search.get("merge") ?? ""} vehicle={data} preselected={search.get("merge")} />
              <MergesCard vehicle={data} split />
            </>
          ) : null}
          {settingsPart === "history" ? <VehicleJournal vehicle={data} /> : null}
        </>
      ) : null}
      {tab === "service" ? (
        <>
          <VehicleService vehicle={data} />
          <VehicleParts vehicle={data} />
          {/* The damages of the vehicle are a matter of its upkeep (customer's remark of
              24.11.2026); an inspection shows its own damages in the tab of inspections. */}
          <VehicleDamagesCard vehicle={data} />
        </>
      ) : null}
      {/* The inspections and, beside them, the damages on the scheme of the vehicle — one tab
          (decision of 12.11.2026). */}
      {tab === "inspections" ? (
        <>
          {/* Inspections started on this device and not sent yet (§ 10.4.2–10.4.4). */}
          <DraftsPanel scope={draftScope} base={companyPath(company)} zone={timeZone} canCreate={can("inspection.create")} />
          <VehicleInspectionsSplit vehicle={data} zone={timeZone} />
        </>
      ) : null}
      {part === "rentals" && can("rental.view") ? <VehicleRentalsSplit rentals={own} zone={timeZone} /> : null}
      {part === "moves" ? (
        <>
          {isClient ? null : <VehicleMoves vehicle={data} />}
          {data.relation === "own" && !stored && data.contexts.length === 0 ? (
            <TransferToWarehouse vehicle={data} />
          ) : null}
          {stored && data.state === "active" ? <ReturnToGarage vehicle={data} /> : null}
        </>
      ) : null}
      {part === "ownership" ? (
        <>
          <HistoryCard vehicle={data} />
          {/* Only records stand in the history: nothing here changes the card (customer's
              decision of 24.11.2026). */}
          <DuplicatesHistory vehicle={data} />
        </>
      ) : null}
      {part === "finance" ? <VehicleFinance vehicle={data} /> : null}
    </div>
  );
}

/** A vehicle in a warehouse goes back to the garage as the same vehicle with its history
 * (§ 8.4.1); operational contexts are allowed afterwards. */
function ReturnToGarage({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const back = useCommand<{ expected_version: number }, unknown>(`/c/${company.public_id}/vehicles/${vehicle.id}/to-garage`);
  if (!can("vehicle.transfer_to_warehouse")) return null;
  return (
    <Card title={t("vehicles.garage_title")}>
      <p>{t("vehicles.garage_text")}</p>
      <ErrorMessage error={back.error} />
      <div className="actions">
        <button
          type="button"
          disabled={back.pending}
          onClick={async () => {
            if ((await back.run({ expected_version: vehicle.version })) !== null) {
              await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
              await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
            }
          }}
        >
          {t("vehicles.garage_button")}
        </button>
      </div>
    </Card>
  );
}

/** Offered when an own vehicle has no operational context: transfer to a warehouse as the same
 * asset with all its history (decision of 24.10.2026; § 8.1.5, § 8.1.7). */
function TransferToWarehouse({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const locations = useLocations();
  const [target, setTarget] = useState("");
  const transfer = useCommand<{ context_id: string; expected_version: number }, unknown>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/to-warehouse`,
  );
  if (!can("vehicle.transfer_to_warehouse")) return null;
  const warehouses = (locations.data?.locations ?? []).flatMap((l) =>
    l.state === "deactivated"
      ? []
      : l.contexts
          .filter((c) => c.type === "warehouse" && c.state === "enabled")
          .map((c) => ({ id: c.id, name: l.name })),
  );
  return (
    <Card title={t("vehicles.transfer_title")}>
      <p>{t("vehicles.transfer_text")}</p>
      {warehouses.length === 0 ? (
        <p className="muted">{t("vehicles.transfer_no_warehouse")}</p>
      ) : (
        <div className="row">
          <Field label={t("vehicles.transfer_target")}>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">—</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      <ErrorMessage error={transfer.error} />
      {warehouses.length ? (
        <div className="actions">
          <button
            type="button"
            disabled={!target || transfer.pending}
            onClick={async () => {
              if ((await transfer.run({ context_id: target, expected_version: vehicle.version })) !== null) {
                await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
                await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
              }
            }}
          >
            {t("vehicles.transfer_button")}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

/** Tariffs assigned to this vehicle with their terms. */
/** The cost of acquisition of an own vehicle, for its payback (§ 21.4.2; decision О-3). */
