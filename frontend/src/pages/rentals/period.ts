import { useRef } from "react";

import { api } from "../../api/client";
import { useCompany } from "../../layout/CompanyShell";
import { fromLocalInput } from "../../time";

type Values = {
  vehicleId: string;
  tariffId: string;
  locationId: string;
  zone: string;
  /** Values of fields of a date and a time, in the zone. */
  start: string;
  end: string;
  units: string;
};

/** The period in units of the tariff, both ways (decision of 15.11.2026): a number of units
 * gives the end, an end gives the number of units. Counted by the server, as the price is.
 * Nothing is answered while a value is missing, on a mistake, or when a later question is out. */
export function usePeriodCount() {
  const company = useCompany();
  const asked = useRef(0);
  return async function count(by: "units" | "end", values: Values): Promise<{ units: number; end: string } | null> {
    const given = by === "units" ? values.units : values.end;
    if (!values.vehicleId || !values.tariffId || !values.start || !given) return null;
    const turn = ++asked.current;
    try {
      const answer = await api<{ units: number; end: string }>(`/c/${company.public_id}/rentals/period`, {
        method: "POST",
        body: {
          vehicle_card_id: values.vehicleId,
          tariff_id: values.tariffId,
          issue_location_id: values.locationId,
          start: fromLocalInput(values.start, values.zone),
          ...(by === "units" ? { units: Number(values.units) } : { end: fromLocalInput(values.end, values.zone) }),
        },
      });
      return turn === asked.current ? answer : null;
    } catch {
      // The period stays as it was entered; the server names the mistake on saving.
      return null;
    }
  };
}
