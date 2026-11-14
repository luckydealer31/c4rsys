/** A small side view of the vehicle by its type and body (customer's decision of 14.11.2026):
 * in a list it tells at a glance what a «Honda Forza» or a «Volkswagen Polo» is. The pictures
 * are drawn here, not taken from elsewhere. */
import { useTranslation } from "react-i18next";

type Shape = { body: string; wheels?: [number, number]; radius?: number };

// 40 × 24, the vehicle looks to the right; the wheels stand at x = 11 and x = 29 unless said.
const car = (points: string, extra = "") => `M${points} M14 18H26 ${extra}`;
const SHAPES: Record<string, Shape> = {
  sedan: { body: car("8 18H3L2 14L3 12L10 11L15 6H25L30 11L36 12L38 14V18H32", "M12 11H28") },
  hatchback: { body: car("8 18H3L2 13L6 6H24L30 11L36 12L38 14V18H32", "M5 11H28") },
  wagon: { body: car("8 18H3L2 12L3 6H25L30 11L36 12L38 14V18H32", "M3 11H28") },
  suv: { body: car("8 18H3L2 10L3 4.5H25L30 10L36 11L38 13V18H32", "M3 10H28"), radius: 3.5 },
  crossover: { body: car("8 18H3L2 11L5 5.5H24L30 10.5L36 11.5L38 13.5V18H32", "M4 10.5H28") },
  coupe: { body: car("8 18H3L2 14L4 12L10 10.5L17 7H24L31 11.5L36 12.5L38 14.5V18H32", "M13 11H29") },
  convertible: { body: car("8 18H3L2 14L3 12H31L36 13L38 14.5V18H32", "M22 12L25.5 7") },
  minivan: { body: car("8 18H3L2 10L3 5H22L31 10L36 11.5L38 13.5V18H32", "M3 10H29") },
  van: { body: car("8 18H3L2 16V5H26L32 10L37 11.5L38 14V18H32", "M24 10H31M24 5V10") },
  pickup: { body: car("8 18H3L2 14V11H17V5.5H25L30 11L36 12L38 14V18H32", "M17 11H29") },
  truck: {
    body: "M2 3H24V14H2Z M26 15V7H32L36 11L38 12V15 M2 15H38 M26 11H35",
    wheels: [9, 31],
  },
  bus: {
    body: "M7 18H4A2 2 0 0 1 2 16V6A2 2 0 0 1 4 4H34A4 4 0 0 1 38 8V16A2 2 0 0 1 36 18H33 M13 18H27 M2 10H38",
    wheels: [10, 30],
  },
  motorcycle: {
    body: "M15 9H23L26 12H13Z M8.5 9.5H15 M32 17L27 6H24 M15 12L17 16H23L26 12 M8 17L13 12",
    wheels: [8, 32],
    radius: 4,
  },
  moped: {
    body: "M32 18L27.5 5H24.5 M3.5 9.5H16 M5 9.5C5 12 6 13.5 8 14.5 M16 9.5L13.5 17.5H25L30.5 12",
    wheels: [8, 32],
  },
};

/** The shape for a type and a body: a passenger car is shown by its body; a truck with the body
 * of a pickup or a van is shown as one. Without data there is no picture. */
function shapeOf(type: string, body: string): string | null {
  if (type === "motorcycle" || type === "moped" || type === "bus") return type;
  if (type === "truck") return body === "pickup" || body === "van" ? body : "truck";
  if (body && SHAPES[body]) return body;
  return type === "passenger_car" ? "sedan" : null;
}

export function VehicleTypeIcon({ type, body, size = 44 }: { type: string; body: string; size?: number }) {
  const { t } = useTranslation();
  const key = shapeOf(type, body);
  const shape = key ? SHAPES[key] : null;
  // The place is kept also without a picture, so that the names in a list stand in one column.
  if (!shape) return <span className="vehicle-type-icon" style={{ width: size }} aria-hidden="true" />;
  const label = [type ? t(`vehicles.type_${type}`) : "", body && body !== "other" ? t(`vehicles.body.${body}`) : ""]
    .filter(Boolean)
    .join(", ");
  const [rear, front] = shape.wheels ?? [11, 29];
  const radius = shape.radius ?? 3;
  return (
    <svg
      className="vehicle-type-icon"
      width={size}
      height={(size * 24) / 40}
      viewBox="0 0 40 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      <path d={shape.body} />
      <circle cx={rear} cy={shape.radius === 4 ? 17 : 18} r={radius} />
      <circle cx={front} cy={shape.radius === 4 ? 17 : 18} r={radius} />
    </svg>
  );
}
