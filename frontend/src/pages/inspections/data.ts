/** Reads and helpers of inspections and damages (ТЗ гл. 10). */
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type {
  Damage,
  Inspection,
  InspectionPolicy,
  InspectionTemplate,
  JournalEntry,
  TemplatePosition,
  Vehicle,
} from "../../api/types";
import { useCompany } from "../../layout/CompanyShell";
import { schemeFor, type SchemeId } from "./schemes";

export const KINDS = ["issue", "return", "out_of_rental", "intermediate"] as const;
export const APPLICATIONS = ["rental", "service", "standalone"] as const;
export const STATES = ["draft", "planned", "in_progress", "saved", "cancelled", "terminated", "invalid"] as const;
/** Kinds allowed for an application (§ 10.1.1; the database constraints of the inspection). */
export const KINDS_OF: Record<(typeof APPLICATIONS)[number], (typeof KINDS)[number][]> = {
  rental: ["issue", "return", "intermediate"],
  service: ["return", "issue", "intermediate", "out_of_rental"],
  standalone: ["out_of_rental", "issue", "return"],
};
/** § 10.4.2: photos JPEG, PNG, WebP up to 5 MB; videos MP4 and MOV up to 2 minutes. */
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
export const MAX_VIDEO_SECONDS = 120;
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function useInspections(query: string) {
  const company = useCompany();
  return useApi<{ inspections: Inspection[] }>(
    ["inspections", company.public_id, query],
    `/c/${company.public_id}/inspections${query ? `?${query}` : ""}`,
  );
}

export function useInspection(id: string) {
  const company = useCompany();
  return useApi<{ inspection: Inspection }>(
    ["inspection", company.public_id, id],
    `/c/${company.public_id}/inspections/${id}`,
    Boolean(id),
  );
}

export function useInspectionHistory(id: string, enabled: boolean) {
  const company = useCompany();
  return useApi<{ history: JournalEntry[] }>(
    ["inspection-history", company.public_id, id],
    `/c/${company.public_id}/inspections/${id}/history`,
    enabled,
  );
}

/** Saved and invalid inspections of the vehicle with their photos (§ 10.3.7). */
export function useVehicleInspections(cardId: string | undefined) {
  const company = useCompany();
  return useApi<{ inspections: Inspection[] }>(
    ["vehicle-inspections", company.public_id, cardId ?? ""],
    `/c/${company.public_id}/vehicles/${cardId ?? ""}/inspections`,
    Boolean(cardId),
  );
}

export function useVehicleDamages(cardId: string | undefined) {
  const company = useCompany();
  return useApi<{ damages: Damage[] }>(
    ["vehicle-damages", company.public_id, cardId ?? ""],
    `/c/${company.public_id}/vehicles/${cardId ?? ""}/damages`,
    Boolean(cardId),
  );
}

export function useDamage(id: string) {
  const company = useCompany();
  return useApi<{ damage: Damage }>(["damage", company.public_id, id], `/c/${company.public_id}/damages/${id}`);
}

export function useTemplates() {
  const company = useCompany();
  return useApi<{ templates: InspectionTemplate[] }>(
    ["inspection-templates", company.public_id],
    `/c/${company.public_id}/inspection-templates`,
  );
}

export function usePolicies() {
  const company = useCompany();
  return useApi<{ policies: InspectionPolicy[] }>(
    ["inspection-policies", company.public_id],
    `/c/${company.public_id}/inspection-policies`,
  );
}

/** After a change every read of inspections and damages is fetched again. */
export function useRefreshInspections() {
  const client = useQueryClient();
  return async () => {
    for (const key of [
      "inspections",
      "inspection",
      "inspection-history",
      "vehicle-inspections",
      "vehicle-damages",
      "damage",
      "rental",
      "rental-history",
      "service-operation",
      "inspection-templates",
      "inspection-policies",
    ]) {
      await client.invalidateQueries({ queryKey: [key] });
    }
  };
}

/** The name of a photo position: base angles are translated, own ones carry their name. */
export function usePositionName(): (position: Pick<TemplatePosition, "code" | "name"> | string) => string {
  const { t } = useTranslation();
  return (position) => {
    const code = typeof position === "string" ? position : position.code;
    const name = typeof position === "string" ? undefined : position.name;
    return name || t(`inspections.position_${code}`, { defaultValue: code });
  };
}

export function vehicleScheme(vehicle: Vehicle | undefined): SchemeId | null {
  if (!vehicle) return null;
  return schemeFor(vehicle.characteristics.vehicle_type, vehicle.characteristics.body_type);
}

/** The duration of a chosen video read by the browser; null when it cannot tell (the server
 * measures it again). */
export function videoDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const element = document.createElement("video");
    element.preload = "metadata";
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    element.onloadedmetadata = () => done(Number.isFinite(element.duration) ? element.duration : null);
    element.onerror = () => done(null);
    window.setTimeout(() => done(null), 15000);
    element.src = url;
  });
}

/** A refusal made in the browser, shown as the server's one would be. */
export function localError(code: string, params: Record<string, unknown> = {}): ApiError {
  return new ApiError(400, { code, message_key: "", params, field: "file", request_id: "" });
}

export function isVideoFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return file.type === "video/mp4" || file.type === "video/quicktime" || name.endsWith(".mp4") || name.endsWith(".mov");
}

export function stateBadge(state: string): string {
  if (state === "saved" || state === "fixed" || state === "ready") return "ok";
  if (state === "invalid" || state === "erroneous" || state === "failed") return "danger";
  if (state === "cancelled" || state === "terminated") return "";
  return "warn";
}
