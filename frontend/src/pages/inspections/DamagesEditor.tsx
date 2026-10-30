/** Damages of an inspection (§ 10.3.6, § 10.5): a tap on the scheme of the vehicle adds a damage
 * with its mark; without a fitting scheme the zone is described by text. A fixed damage seen
 * again is either returned or recorded as a new one — the employee chooses (§ 10.5.1). */
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Damage, DamageDraftEntry, InspectionFileItem } from "../../api/types";
import { Field } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { stateBadge } from "./data";
import { PhotoThumb } from "./Media";
import { VehicleScheme, type SchemeId, type SchemeMark } from "./schemes";

export function DamagesEditor({
  scheme,
  entries,
  onChange,
  photos,
  known,
  rental,
}: {
  scheme: SchemeId | null;
  entries: DamageDraftEntry[];
  onChange: (entries: DamageDraftEntry[]) => void;
  photos: InspectionFileItem[];
  known: Damage[];
  rental: boolean;
}) {
  const { t } = useTranslation();
  const fixed = known.filter((d) => d.state === "fixed");
  const actual = known.filter((d) => d.state === "actual");
  const fresh = (patch: Partial<DamageDraftEntry>): DamageDraftEntry => ({
    zone_scheme: "",
    zone_x: null,
    zone_y: null,
    zone_text: "",
    description: "",
    photo_file_ids: [],
    // § 10.5.3: whether a damage of a rental inspection is charged in the rental is chosen
    // explicitly; there is no default.
    ...(rental ? { attributed_to_rental: undefined } : {}),
    ...patch,
  });
  const update = (index: number, patch: Partial<DamageDraftEntry>) =>
    onChange(entries.map((e, i) => (i === index ? { ...e, ...patch } : e)));
  const remove = (index: number) => onChange(entries.filter((_, i) => i !== index));
  const marks: SchemeMark[] = entries
    .map((e, index) => ({ e, index }))
    .filter(({ e }) => e.zone_scheme && e.zone_x !== null && e.zone_x !== undefined)
    .map(({ e, index }) => ({ x: Number(e.zone_x), y: Number(e.zone_y), view: null, label: String(index + 1) }));

  return (
    <div className="stack">
      {scheme ? (
        <>
          <p className="muted small">{t("inspections.scheme_hint")}</p>
          <div className="scheme-box">
            <VehicleScheme
              id={scheme}
              marks={marks}
              label={(view) => t(`inspections.view_${view}`)}
              onMark={(mark) =>
                onChange([
                  ...entries,
                  fresh({ zone_scheme: scheme, zone_x: mark.x.toFixed(4), zone_y: mark.y.toFixed(4) }),
                ])
              }
            />
          </div>
        </>
      ) : (
        <p className="alert warn">{t("inspections.no_scheme")}</p>
      )}
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="secondary" onClick={() => onChange([...entries, fresh({})])}>
          {t("inspections.add_damage_text")}
        </button>
      </div>
      {entries.map((entry, index) =>
        entry.returns_damage_id ? (
          <ReturnedEntry
            key={index}
            index={index}
            entry={entry}
            damage={known.find((d) => d.id === entry.returns_damage_id)}
            photos={photos}
            onChange={(patch) => update(index, patch)}
            onRemove={() => remove(index)}
          />
        ) : (
          <div key={index} className="damage-entry">
            <div className="position-head">
              <span className="damage-number">{index + 1}</span>
              <strong>{entry.zone_scheme ? t("inspections.damage_on_scheme") : t("inspections.damage_by_text")}</strong>
              <button type="button" className="secondary small-button" onClick={() => remove(index)}>
                {t("inspections.remove_damage")}
              </button>
            </div>
            <Field label={t("inspections.damage_description")}>
              <textarea
                rows={2}
                maxLength={2000}
                value={entry.description ?? ""}
                onChange={(e) => update(index, { description: e.target.value })}
              />
            </Field>
            <Field label={t("inspections.damage_zone_text")} hint={entry.zone_scheme ? undefined : t("inspections.zone_text_hint")}>
              <input maxLength={300} value={entry.zone_text ?? ""} onChange={(e) => update(index, { zone_text: e.target.value })} />
            </Field>
            {rental ? (
              <Field label={t("inspections.attributed_question")} hint={t("inspections.attributed_hint")}>
                <select
                  value={entry.attributed_to_rental === undefined ? "" : entry.attributed_to_rental ? "yes" : "no"}
                  onChange={(e) =>
                    update(index, { attributed_to_rental: e.target.value === "" ? undefined : e.target.value === "yes" })
                  }
                >
                  <option value="">{t("inspections.choose")}</option>
                  <option value="yes">{t("inspections.attributed_yes")}</option>
                  <option value="no">{t("inspections.attributed_no")}</option>
                </select>
              </Field>
            ) : null}
            <PhotoChoice entry={entry} photos={photos} onChange={(ids) => update(index, { photo_file_ids: ids })} />
          </div>
        ),
      )}
      {fixed.length ? (
        <div className="stack">
          <h3>{t("inspections.fixed_damages")}</h3>
          <p className="muted small">{t("inspections.return_hint")}</p>
          {fixed.map((d) => {
            const chosen = entries.some((e) => e.returns_damage_id === d.id);
            return (
              <div key={d.id} className="row damage-known">
                <span>{damageText(d) || t("inspections.damage_on_scheme")}</span>
                <button
                  type="button"
                  className="secondary"
                  disabled={chosen}
                  onClick={() => onChange([...entries, { returns_damage_id: d.id, photo_file_ids: [] }])}
                >
                  {chosen ? t("inspections.returned_chosen") : t("inspections.return_damage")}
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      {actual.length ? (
        <details>
          <summary className="muted small">{t("inspections.actual_damages", { count: actual.length })}</summary>
          <DamageList damages={actual} />
        </details>
      ) : null}
    </div>
  );
}

function ReturnedEntry({
  index,
  entry,
  damage,
  photos,
  onChange,
  onRemove,
}: {
  index: number;
  entry: DamageDraftEntry;
  damage: Damage | undefined;
  photos: InspectionFileItem[];
  onChange: (patch: Partial<DamageDraftEntry>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="damage-entry">
      <div className="position-head">
        <span className="damage-number">{index + 1}</span>
        <strong>{t("inspections.returned_damage")}</strong>
        <button type="button" className="secondary small-button" onClick={onRemove}>
          {t("inspections.remove_damage")}
        </button>
      </div>
      <p>{damage ? damageText(damage) : "—"}</p>
      <PhotoChoice entry={entry} photos={photos} onChange={(ids) => onChange({ photo_file_ids: ids })} />
    </div>
  );
}

/** Photos of this inspection chosen for a damage (§ 10.5.2). */
function PhotoChoice({
  entry,
  photos,
  onChange,
}: {
  entry: DamageDraftEntry;
  photos: InspectionFileItem[];
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  if (!photos.length) return <p className="muted small">{t("inspections.damage_photos_first")}</p>;
  return (
    <div>
      <div className="muted small">{t("inspections.damage_photos")}</div>
      <div className="media-row">
        {photos.map((p) => {
          const chosen = entry.photo_file_ids.includes(p.file_id);
          return (
            <label key={p.file_id} className={`photo-choice${chosen ? " chosen" : ""}`}>
              <input
                type="checkbox"
                checked={chosen}
                onChange={() =>
                  onChange(chosen ? entry.photo_file_ids.filter((i) => i !== p.file_id) : [...entry.photo_file_ids, p.file_id])
                }
              />
              <PhotoThumb fileId={p.file_id} size={72} />
            </label>
          );
        })}
      </div>
    </div>
  );
}

export function damageText(damage: Pick<Damage, "description" | "zone_text">): string {
  return [damage.zone_text, damage.description].filter(Boolean).join(" · ");
}

export function DamageList({ damages, numbered }: { damages: Damage[]; numbered?: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  return (
    <ul className="damage-list">
      {damages.map((d, index) => (
        <li key={d.id}>
          {numbered ? <span className="damage-number">{index + 1}</span> : null}
          <Link to={companyPath(company, `/damages/${d.id}`)}>{damageText(d) || t("inspections.damage_on_scheme")}</Link>{" "}
          <span className={`badge ${stateBadge(d.state)}`}>{t(`inspections.damage_state_${d.state}`)}</span>
          {d.attributed_to_rental ? <span className="muted small"> · {t("inspections.attributed_short")}</span> : null}
        </li>
      ))}
    </ul>
  );
}
