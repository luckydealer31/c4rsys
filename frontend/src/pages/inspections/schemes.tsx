/** Schemes of vehicles for marking damages (§ 10.3.6, decision В-8.2): simple outlines drawn per
 * kind of transport and body type. A mark is kept as a point relative to the whole scheme (0..1)
 * with the view it falls in, so a scheme can later be redrawn without losing the marks. */

export type SchemeId =
  | "sedan"
  | "hatchback"
  | "wagon"
  | "suv"
  | "coupe"
  | "convertible"
  | "pickup"
  | "van"
  | "truck"
  | "bus"
  | "motorcycle"
  | "scooter";

export const SCHEMES: SchemeId[] = [
  "sedan",
  "hatchback",
  "wagon",
  "suv",
  "coupe",
  "convertible",
  "pickup",
  "van",
  "truck",
  "bus",
  "motorcycle",
  "scooter",
];

export type View = "left" | "right" | "top" | "front" | "rear";
export type SchemeMark = { x: number; y: number; view: View | null; label?: string };

type Point = [number, number];

/** The scheme of a vehicle card; none when no ready scheme fits (§ 10.3.6: text zone and photos). */
export function schemeFor(vehicleType: string, bodyType: string): SchemeId | null {
  if (vehicleType === "truck") return "truck";
  if (vehicleType === "bus") return "bus";
  if (vehicleType === "motorcycle") return "motorcycle";
  if (vehicleType === "moped") return "scooter";
  if (vehicleType !== "passenger_car") return null;
  const byBody: Record<string, SchemeId> = {
    sedan: "sedan",
    hatchback: "hatchback",
    wagon: "wagon",
    suv: "suv",
    crossover: "suv",
    coupe: "coupe",
    convertible: "convertible",
    pickup: "pickup",
    minivan: "van",
    van: "van",
  };
  return byBody[bodyType] ?? "sedan";
}

type Side = { body: Point[]; glass?: Point[]; wheels: [number, number] };

// Side silhouettes of cars in a 300 × 100 box, the front on the left; wheels stand on y = 92.
const SIDES: Partial<Record<SchemeId, Side>> & { sedan: Side } = {
  sedan: {
    body: [[8, 78], [14, 58], [78, 50], [108, 26], [190, 24], [228, 48], [288, 52], [294, 78]],
    glass: [[112, 30], [150, 29], [150, 48], [86, 50]],
    wheels: [62, 236],
  },
  hatchback: {
    body: [[8, 78], [14, 58], [72, 50], [104, 26], [228, 24], [262, 44], [270, 78]],
    glass: [[108, 30], [160, 29], [160, 48], [82, 50]],
    wheels: [58, 214],
  },
  wagon: {
    body: [[8, 78], [14, 58], [78, 50], [108, 26], [270, 24], [286, 46], [292, 78]],
    glass: [[112, 30], [160, 29], [160, 48], [86, 50]],
    wheels: [62, 240],
  },
  suv: {
    body: [[8, 80], [12, 46], [70, 40], [98, 12], [272, 12], [288, 34], [292, 80]],
    glass: [[102, 16], [170, 16], [170, 38], [80, 40]],
    wheels: [62, 240],
  },
  coupe: {
    body: [[8, 78], [14, 58], [88, 50], [128, 28], [180, 26], [240, 48], [290, 52], [294, 78]],
    glass: [[132, 32], [176, 30], [200, 46], [100, 49]],
    wheels: [62, 238],
  },
  convertible: {
    body: [[8, 78], [14, 58], [88, 50], [118, 38], [124, 46], [240, 46], [290, 50], [294, 78]],
    wheels: [62, 238],
  },
  pickup: {
    body: [[8, 80], [12, 50], [64, 44], [92, 14], [160, 14], [164, 44], [290, 44], [292, 80]],
    glass: [[96, 18], [156, 18], [156, 40], [74, 42]],
    wheels: [60, 236],
  },
  van: {
    body: [[8, 82], [12, 42], [46, 14], [290, 10], [292, 82]],
    glass: [[50, 18], [100, 16], [100, 40], [22, 42]],
    wheels: [58, 240],
  },
  truck: {
    body: [[8, 84], [10, 30], [24, 8], [74, 8], [74, 26], [84, 4], [292, 4], [292, 84]],
    glass: [[28, 12], [70, 12], [70, 36], [14, 36]],
    wheels: [48, 250],
  },
  bus: {
    body: [[6, 86], [8, 8], [292, 8], [294, 86]],
    glass: [[14, 16], [286, 16], [286, 42], [14, 42]],
    wheels: [56, 246],
  },
};

type Box = { view: View; x: number; y: number; w: number; h: number };

const CAR_BOXES: Box[] = [
  { view: "left", x: 10, y: 10, w: 300, h: 100 },
  { view: "right", x: 330, y: 10, w: 300, h: 100 },
  { view: "top", x: 10, y: 130, w: 300, h: 120 },
  { view: "front", x: 330, y: 130, w: 140, h: 120 },
  { view: "rear", x: 490, y: 130, w: 140, h: 120 },
];
const BIKE_BOXES: Box[] = [
  { view: "left", x: 10, y: 10, w: 300, h: 160 },
  { view: "right", x: 330, y: 10, w: 300, h: 160 },
];
const SIZE = { car: { w: 640, h: 260 }, bike: { w: 640, h: 180 } };

const isBike = (id: SchemeId) => id === "motorcycle" || id === "scooter";

/** The view a relative point falls in. */
export function viewAt(id: SchemeId, x: number, y: number): View | null {
  const size = isBike(id) ? SIZE.bike : SIZE.car;
  const px = x * size.w;
  const py = y * size.h;
  const box = (isBike(id) ? BIKE_BOXES : CAR_BOXES).find(
    (b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h,
  );
  return box?.view ?? null;
}

const poly = (points: Point[]) => points.map(([x, y]) => `${x},${y}`).join(" ");

function CarSide({ id, mirror }: { id: SchemeId; mirror: boolean }) {
  const side = SIDES[id] ?? SIDES.sedan;
  return (
    <g transform={mirror ? "translate(300,0) scale(-1,1)" : undefined}>
      <polygon points={poly(side.body)} className="scheme-body" />
      {side.glass ? <polygon points={poly(side.glass)} className="scheme-glass" /> : null}
      {side.wheels.map((cx) => (
        <circle key={cx} cx={cx} cy={84} r={14} className="scheme-wheel" />
      ))}
    </g>
  );
}

function CarTop({ id }: { id: SchemeId }) {
  const long = id === "truck" || id === "bus" || id === "van";
  const open = id === "convertible" || id === "pickup";
  return (
    <g>
      <rect x={6} y={14} width={288} height={92} rx={long ? 8 : 28} className="scheme-body" />
      {id === "bus" ? (
        <rect x={20} y={24} width={260} height={72} rx={4} className="scheme-glass" />
      ) : (
        <>
          <polygon points="88,24 112,32 112,88 88,96" className="scheme-glass" />
          {open ? null : <rect x={116} y={30} width={id === "truck" ? 20 : 100} height={60} rx={6} className="scheme-roof" />}
          {id === "pickup" ? <rect x={170} y={22} width={118} height={76} rx={4} className="scheme-roof" /> : null}
          {id === "truck" ? <rect x={84} y={16} width={208} height={88} rx={2} className="scheme-roof" /> : null}
          {id !== "truck" && id !== "pickup" ? (
            <polygon points={long ? "272,28 262,32 262,88 272,92" : "232,28 218,34 218,86 232,92"} className="scheme-glass" />
          ) : null}
        </>
      )}
      {[34, 266].map((x) => (
        <g key={x}>
          <rect x={x - 14} y={6} width={28} height={10} rx={3} className="scheme-wheel" />
          <rect x={x - 14} y={104} width={28} height={10} rx={3} className="scheme-wheel" />
        </g>
      ))}
    </g>
  );
}

function CarEnd({ id, rear }: { id: SchemeId; rear: boolean }) {
  const tall = id === "suv" || id === "van" || id === "truck" || id === "bus";
  const top = tall ? 10 : 34;
  return (
    <g>
      <rect x={10} y={top} width={120} height={100 - top} rx={12} className="scheme-body" />
      {id === "convertible" && !rear ? null : (
        <rect x={24} y={top + 8} width={92} height={tall ? 34 : 22} rx={6} className="scheme-glass" />
      )}
      <rect x={18} y={88} width={20} height={8} rx={2} className={rear ? "scheme-light-rear" : "scheme-light"} />
      <rect x={102} y={88} width={20} height={8} rx={2} className={rear ? "scheme-light-rear" : "scheme-light"} />
      <rect x={14} y={100} width={22} height={14} rx={3} className="scheme-wheel" />
      <rect x={104} y={100} width={22} height={14} rx={3} className="scheme-wheel" />
    </g>
  );
}

function BikeSide({ id, mirror }: { id: SchemeId; mirror: boolean }) {
  const scooter = id === "scooter";
  return (
    <g transform={mirror ? "translate(300,0) scale(-1,1)" : undefined}>
      <circle cx={60} cy={120} r={scooter ? 26 : 34} className="scheme-wheel" />
      <circle cx={240} cy={120} r={scooter ? 26 : 34} className="scheme-wheel" />
      {scooter ? (
        <>
          <polygon points="70,40 92,40 108,104 200,104 220,80 268,80 268,104 250,120 96,124 66,118" className="scheme-body" />
          <polygon points="150,70 250,66 262,80 212,82" className="scheme-roof" />
          <line x1={74} y1={42} x2={58} y2={20} className="scheme-line" />
          <line x1={46} y1={20} x2={70} y2={20} className="scheme-line" />
        </>
      ) : (
        <>
          <polygon points="74,72 120,58 180,62 210,74 262,70 270,84 214,96 150,110 104,104" className="scheme-body" />
          <polygon points="186,58 250,56 262,66 196,72" className="scheme-roof" />
          <line x1={60} y1={120} x2={92} y2={46} className="scheme-line" />
          <line x1={80} y1={44} x2={104} y2={44} className="scheme-line" />
          <rect x={120} y={88} width={56} height={26} rx={6} className="scheme-glass" />
        </>
      )}
    </g>
  );
}

/** A scheme with its marks; a click puts a new mark when `onMark` is given. */
export function VehicleScheme({
  id,
  marks,
  onMark,
  label,
}: {
  id: SchemeId;
  marks: SchemeMark[];
  onMark?: (mark: SchemeMark) => void;
  label?: (view: View) => string;
}) {
  const bike = isBike(id);
  const size = bike ? SIZE.bike : SIZE.car;
  const boxes = bike ? BIKE_BOXES : CAR_BOXES;
  const click = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!onMark) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    onMark({ x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000, view: viewAt(id, x, y) });
  };
  return (
    <svg
      viewBox={`0 0 ${size.w} ${size.h}`}
      className="vehicle-scheme"
      role="img"
      onClick={click}
      style={{ width: "100%", height: "auto", cursor: onMark ? "crosshair" : "default" }}
    >
      {boxes.map((b) => (
        <g key={b.view} transform={`translate(${b.x},${b.y})`}>
          {b.view === "left" || b.view === "right" ? (
            bike ? (
              <BikeSide id={id} mirror={b.view === "right"} />
            ) : (
              <CarSide id={id} mirror={b.view === "right"} />
            )
          ) : b.view === "top" ? (
            <CarTop id={id} />
          ) : (
            <CarEnd id={id} rear={b.view === "rear"} />
          )}
          {label ? (
            <text x={4} y={b.h - 2} className="scheme-caption">
              {label(b.view)}
            </text>
          ) : null}
        </g>
      ))}
      {marks.map((m, index) => (
        <g key={index} transform={`translate(${m.x * size.w},${m.y * size.h})`}>
          <circle r={9} className="scheme-mark" />
          <text y={4} textAnchor="middle" className="scheme-mark-text">
            {m.label ?? index + 1}
          </text>
        </g>
      ))}
    </svg>
  );
}
