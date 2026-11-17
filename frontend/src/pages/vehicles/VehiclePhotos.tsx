import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { fileUrl } from "../../api/client";
import type { Vehicle } from "../../api/types";
import { shapeOf, VehicleTypeIcon } from "../../components/VehicleTypeIcon";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useInspections } from "../inspections/data";

// The angles shown, in the order of a walk around the vehicle, the dashboard last.
const ANGLES = ["front", "left", "rear", "right", "odometer"];

/** The photos of the vehicle in its card (customer's decisions of 17.11.2026): the five main
 * angles of the last inspection conducted, of any kind — one large, the rest small under it; a
 * small one pressed becomes the large one. While there are no photos, the picture of the body
 * type stands in their place. */
export function VehiclePhotos({ vehicle }: { vehicle: Vehicle }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const list = useInspections(`vehicle_card_id=${vehicle.id}`);
  const [chosen, setChosen] = useState("");
  // The list comes from the newest; the last inspection that has such photos is taken.
  const photo = (files: { kind: string; position: string | null; file_id: string }[], angle: string) =>
    files.find((f) => f.kind === "photo" && f.position === angle);
  const last = (list.data?.inspections ?? []).find(
    (i) => i.state === "saved" && ANGLES.some((angle) => photo(i.files, angle)),
  );
  const photos = last
    ? ANGLES.flatMap((angle) => {
        const file = photo(last.files, angle);
        return file ? [{ angle, id: file.file_id, label: t(`inspections.position_${angle}`) }] : [];
      })
    : [];
  const main = photos.find((p) => p.angle === chosen) ?? photos[0];
  if (list.isLoading) return <div className="vehicle-photos" aria-hidden="true" />;
  if (!last || !main) {
    // Without a type and a body there is no picture, and no empty place for it.
    if (!shapeOf(vehicle.characteristics.vehicle_type, vehicle.characteristics.body_type)) return null;
    return (
      <div className="vehicle-photos">
        <div className="vehicle-photo-main none">
          <VehicleTypeIcon
            type={vehicle.characteristics.vehicle_type}
            body={vehicle.characteristics.body_type}
            size={240}
            vector
          />
        </div>
      </div>
    );
  }
  return (
    <figure className="vehicle-photos">
      <a className="vehicle-photo-main" href={fileUrl(main.id)} target="_blank" rel="noreferrer">
        <img src={fileUrl(main.id)} alt={main.label} />
      </a>
      {photos.length > 1 ? (
        <div className="vehicle-photo-thumbs">
          {photos
            .filter((p) => p !== main)
            .map((p) => (
              <button key={p.angle} type="button" aria-label={p.label} title={p.label} onClick={() => setChosen(p.angle)}>
                <img src={fileUrl(p.id)} alt="" loading="lazy" />
              </button>
            ))}
        </div>
      ) : null}
      <figcaption className="small muted">
        {main.label} ·{" "}
        <Link to={companyPath(company, `/inspections/${last.id}`)}>
          {t("vehicles.photos_from", {
            when: formatMoment(last.performed_at, company.timezone ?? "UTC", i18n.language),
          })}
        </Link>
      </figcaption>
    </figure>
  );
}
