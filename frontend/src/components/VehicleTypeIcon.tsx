/** A small side view of the vehicle by its type and body (customer's decision of 14.11.2026):
 * in a list it tells at a glance what a «Honda Forza» or a «Volkswagen Polo» is. Filled
 * silhouettes with cut-out windows, drawn here after the samples the customer sent (15.11.2026). */
import { useId } from "react";
import { useTranslation } from "react-i18next";

type Shape = { body: string; windows?: string; wheels?: [number, number]; radius?: number; axle?: number };

// 80 × 40, the vehicle looks to the right; the wheels stand at x = 20 and x = 60 unless said.
const SHAPES: Record<string, Shape> = {
  sedan: {
    body: "M4 30C3 27 3 24 5 22C8 20 13 19.5 18 19C23 13 28 9.5 35 9L46 9C52 9.5 57 13 62 18.5C68 19 73 20.5 76 23C77.5 25 77.5 28 76 30Z",
    windows: "M22 18.5C26 14 30 11.5 35 11L39.5 11L39.5 18.5Z M42 11L46 11C50 11.5 54.5 14.5 58 18.5L42 18.5Z",
  },
  hatchback: {
    body: "M5 30C3.5 27 3.5 22 6 18C9 12 14 9 20 9L42 9C49 9.5 55 13 61 18.5C68 19 73 20.5 76 23C77.5 25 77.5 28 76 30Z",
    windows: "M10 18.5C12 14 15 11.5 20 11L27 11L27 18.5Z M29.5 11L42 11C47 11.5 52.5 14.5 57 18.5L29.5 18.5Z",
  },
  wagon: {
    body: "M4 30C3 26 3 20 4.5 15C5.5 11 8 9 12 9L44 9C50 9.5 56 13 62 18.5C68 19 73 20.5 76 23C77.5 25 77.5 28 76 30Z",
    windows: "M7.5 18.5C7.5 15 8.5 11 12 11L20 11L20 18.5Z M22.5 11H36V18.5H22.5Z M38.5 11L44 11C49 11.5 54 14.5 58 18.5L38.5 18.5Z",
  },
  suv: {
    body: "M4 30C3 25 3 14 4 9C4.5 7 6 6 8 6L44 6C48 6 51 8 54 12L58 17C66 17.5 72 19 75.5 21.5C77.5 24 77.5 28 76 30Z",
    windows: "M7 16.5V10C7 8.6 7.8 8 9 8H19V16.5Z M21.5 8H34V16.5H21.5Z M36.5 8H44C46.5 8 49 9.8 51 12.5L54 16.5H36.5Z",
    radius: 6.8,
  },
  crossover: {
    body: "M4 30C3 26 3 20 5.5 15C8 10 12 7.5 18 7.5L43 7.5C49 8 55 12 60 17.5C67 18 73 19.5 76 22.5C77.5 25 77.5 28 76 30Z",
    windows: "M9.5 17.5C11 13 14 9.8 18 9.5L26 9.5V17.5Z M28.5 9.5H43C48 10 52.5 13.5 56 17.5H28.5Z",
    radius: 6.4,
  },
  coupe: {
    body: "M4 30C3 27.5 3 25 5 23C10 20.5 15 20 19 19.5C26 14 32 11 39 11L45 11C51 11.5 56 15 61 19.5C68 20 73 21.5 76 24C77.5 26 77.5 28.5 76 30Z",
    windows: "M25 19.5C30 15.5 34 13 39 13L45 13C49.5 13.5 53.5 16 57 19.5Z",
  },
  convertible: {
    body: "M4 30C3 27.5 3 25 5 23C10 20.5 16 20 22 20L30 20C31 18 33 17 35 17.5L37 20L50 20L55.5 12.5L57.5 13.2L54.5 20C62 20 72 21.5 76 24C77.5 26 77.5 28.5 76 30Z",
  },
  minivan: {
    body: "M4 30C3 26 3 16 4.5 11C5.2 8.5 7 7.5 9 7.5L40 7.5C47 8 55 13 63 19C69 19.5 74 21 76 23.5C77.5 25.5 77.5 28 76 30Z",
    windows: "M7.5 17.5V11.5C7.5 10 8.3 9.5 9.5 9.5H19V17.5Z M21.5 9.5H33V17.5H21.5Z M35.5 9.5H40C45.5 10 51.5 13.5 57.5 17.5H35.5Z",
  },
  van: {
    body: "M4 30V9C4 7 5.5 6 7.5 6H48C52 6 55 8 58 12L62 18C69 18.5 74 20.5 76 23C77.5 25.5 77.5 28 76 30Z",
    windows: "M47 8.5H49.5C52 8.5 54 10 56 12.8L59 17.5H47Z",
  },
  pickup: {
    body: "M4 30C3.5 27 3.5 22 4.5 19.5L34 19.5L36 11.5C36.5 9.8 37.5 9 39.5 9H48C52 9.5 56 13 61 18.5C68 19 73 20.5 76 23C77.5 25 77.5 28 76 30Z",
    windows: "M38.5 11H47.5C50.5 11.5 54 14.5 57.5 18.5H37Z",
  },
  truck: {
    body: "M5 5H48C49.1 5 50 5.9 50 7V26H3V7C3 5.9 3.9 5 5 5Z M3 27H77V31H3Z M53 11H65C67 11 68.5 12 69.5 13.5L74 20C76 21 77 22.5 77 24.5V27H53Z",
    windows: "M56 13.5H64.5C65.5 13.5 66.5 14 67.2 15L70 19.5H56Z",
    wheels: [16, 62],
  },
  bus: {
    body: "M7 5H70C74 5 77 8.5 77 13V27C77 29 75.5 30.5 73.5 30.5H6.5C4.5 30.5 3 29 3 27V9C3 6.5 4.5 5 7 5Z",
    windows: "M6 9H16V17H6Z M19 9H29V17H19Z M32 9H42V17H32Z M45 9H55V17H45Z M58 9H68V17H58Z M70.5 9.5C73 10 74.5 12 74.5 15V18H70.5Z",
    wheels: [18, 62],
  },
  motorcycle: {
    body: "M22 15C26 13 32 13.5 36 15C40 10.5 47 9.5 52 13L55 17C53 23 49 28 44 29H32C28 27 25 22 22 15Z M10 16C15 13.5 21 13.5 25 15.5L24 18.5C19 17 15 17.5 11 19Z M50 7.5H57L67.5 26.5L64.8 27.6L55.5 11L52.5 11.5L50 9.5Z M14 25.5L30 22L31 25L15 28.5Z",
    wheels: [14, 66],
    radius: 8,
    axle: 27,
  },
  moped: {
    body: "M6 17C6 15.5 7 15 8.5 15H30C32 15 33 16 33 17.5V19C33 23 35 25.5 39 25.5H47C50 25.5 52 24 53 21L57 9H53V6.5H60.5L67.5 27L64.5 28L61 18L57.5 27C56.5 30 54 31.5 51 31.5H24C24 25 20 21.5 14 21.5C11 21.5 8.5 22.5 7 24C6 22 6 19.5 6 17Z",
    wheels: [15, 65],
    radius: 5.5,
    axle: 29.5,
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

export function VehicleTypeIcon({ type, body, size = 56 }: { type: string; body: string; size?: number }) {
  const { t } = useTranslation();
  const mask = useId();
  const key = shapeOf(type, body);
  const shape = key ? SHAPES[key] : null;
  // The place is kept also without a picture, so that the names in a list stand in one column.
  if (!shape) return <span className="vehicle-type-icon" style={{ width: size }} aria-hidden="true" />;
  const label = [type ? t(`vehicles.type_${type}`) : "", body && body !== "other" ? t(`vehicles.body.${body}`) : ""]
    .filter(Boolean)
    .join(", ");
  const radius = shape.radius ?? 6;
  const y = shape.axle ?? 35 - radius;
  const wheels = shape.wheels ?? [20, 60];
  return (
    <svg className="vehicle-type-icon" width={size} height={size / 2} viewBox="0 0 80 40" role="img" aria-label={label}>
      <title>{label}</title>
      {/* The windows and the arches of the wheels are cut out of the body. */}
      <mask id={mask}>
        <rect width="80" height="40" fill="#fff" />
        {shape.windows ? <path d={shape.windows} fill="#000" /> : null}
        {wheels.map((x) => (
          <circle key={x} cx={x} cy={y} r={radius + 1.6} fill="#000" />
        ))}
      </mask>
      <path d={shape.body} fill="currentColor" mask={`url(#${mask})`} />
      {wheels.map((x) => (
        <circle key={x} cx={x} cy={y} r={radius} fill="currentColor" />
      ))}
    </svg>
  );
}
