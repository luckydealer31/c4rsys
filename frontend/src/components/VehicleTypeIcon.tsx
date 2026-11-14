/** A small side view of the vehicle by its type and body (customer's decision of 14.11.2026):
 * in a list it tells at a glance what a «Honda Forza» or a «Volkswagen Polo» is. Filled
 * silhouettes with cut-out windows; the pictures are drawn here, not taken from elsewhere. */
import { useId } from "react";
import { useTranslation } from "react-i18next";

type Shape = { body: string; windows?: string; wheels?: [number, number]; radius?: number };

// 40 × 24, the vehicle looks to the right; the wheels stand at x = 11 and x = 29 unless said.
const SHAPES: Record<string, Shape> = {
  sedan: {
    body: "M3 18L2 14L3 12L10 11L15 6H25L30 11L36 12L38 14V18Z",
    windows: "M12 11L15.8 7.2H19.5V11Z M21 7.2H24.5L28 11H21Z",
  },
  hatchback: {
    body: "M3 18L2 13L6 6H24L30 11L36 12L38 14V18Z",
    windows: "M6 11L8 7.2H15V11Z M16.5 7.2H23.5L27.8 11H16.5Z",
  },
  wagon: {
    body: "M3 18L2 12L3 6H25L30 11L36 12L38 14V18Z",
    windows: "M4.5 11L5 7.2H13V11Z M14.5 7.2H24.5L28 11H14.5Z",
  },
  suv: {
    body: "M3 18L2 10L3 4.5H25L30 10L36 11L38 13V18Z",
    windows: "M4.5 10L5 5.8H13V10Z M14.5 5.8H24.5L28 10H14.5Z",
    radius: 3.5,
  },
  crossover: {
    body: "M3 18L2 11L5 5.5H24L30 10.5L36 11.5L38 13.5V18Z",
    windows: "M5.5 10.5L7.2 6.8H14.5V10.5Z M16 6.8H23.5L27.8 10.5H16Z",
  },
  coupe: {
    body: "M3 18L2 14L4 12L10 10.5L17 7H24L31 11.5L36 12.5L38 14.5V18Z",
    windows: "M13 11L17.5 8.2H23.5L28.5 11.4Z",
  },
  convertible: { body: "M3 18L2 14L3 12H22L25 7.2L26 7.6L24 12H31L36 13L38 14.5V18Z" },
  minivan: {
    body: "M3 18L2 10L3 5H22L31 10L36 11.5L38 13.5V18Z",
    windows: "M4.5 10L5 6.2H12V10Z M13.5 6.2H21.5L28.5 10H13.5Z",
  },
  van: { body: "M3 18L2 16V5H26L32 10L37 11.5L38 14V18Z", windows: "M24 6.2H25.6L30.3 10H24Z" },
  pickup: {
    body: "M3 18L2 14V11H17V5.5H25L30 11L36 12L38 14V18Z",
    windows: "M18.5 6.8H24.4L28 11H18.5Z",
  },
  truck: {
    body: "M2 3H24V14H2Z M2 15H38V18H2Z M26 7H32L36 11L38 12V15H26Z",
    windows: "M27.5 8.2H31.5L34.2 11H27.5Z",
    wheels: [9, 31],
  },
  bus: {
    body: "M4 4H34A4 4 0 0 1 38 8V16A2 2 0 0 1 36 18H4A2 2 0 0 1 2 16V6A2 2 0 0 1 4 4Z",
    windows: "M4 6H10V10H4Z M11.5 6H17.5V10H11.5Z M19 6H25V10H19Z M26.5 6H32V10H26.5Z M33.5 6.5H36V10H33.5Z",
    wheels: [10, 30],
  },
  motorcycle: {
    body: "M13 9H23L26 12.5L23 16H17L14 12.5Z M7 9H14L13 11H8Z M24 5H27.5L32.5 16.5L31 17.2L26.5 6.5H24Z M8 17L13 11.5L14.5 12.5L9.5 17.8Z",
    wheels: [8, 32],
    radius: 4,
  },
  moped: {
    body: "M3.5 8.5H16V10.5H15L13.5 16.5H25L29.5 11.5L27 5.8H24.5V4.5H28L33 17.5L31.5 18L30.3 14.5L26 18.5H12L12.5 14C11 12 8 11 4.5 11Z",
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
  const mask = useId();
  const key = shapeOf(type, body);
  const shape = key ? SHAPES[key] : null;
  // The place is kept also without a picture, so that the names in a list stand in one column.
  if (!shape) return <span className="vehicle-type-icon" style={{ width: size }} aria-hidden="true" />;
  const label = [type ? t(`vehicles.type_${type}`) : "", body && body !== "other" ? t(`vehicles.body.${body}`) : ""]
    .filter(Boolean)
    .join(", ");
  const radius = shape.radius ?? 3;
  const y = radius === 4 ? 17 : 18;
  const wheels = shape.wheels ?? [11, 29];
  return (
    <svg className="vehicle-type-icon" width={size} height={(size * 24) / 40} viewBox="0 0 40 24" role="img" aria-label={label}>
      <title>{label}</title>
      {/* The windows and the arches of the wheels are cut out of the body. */}
      <mask id={mask}>
        <rect width="40" height="24" fill="#fff" />
        {shape.windows ? <path d={shape.windows} fill="#000" /> : null}
        {wheels.map((x) => (
          <circle key={x} cx={x} cy={y} r={radius + 1.2} fill="#000" />
        ))}
      </mask>
      <path d={shape.body} fill="currentColor" mask={`url(#${mask})`} />
      {wheels.map((x) => (
        <circle key={x} cx={x} cy={y} r={radius - 0.9} fill="none" stroke="currentColor" strokeWidth="1.8" />
      ))}
    </svg>
  );
}
