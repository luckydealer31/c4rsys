// Load test (audit item 12): employees working in the interface at the same time. Each virtual
// user opens the page and reads the lists an employee opens most, with a pause between screens.
// Only reads: nothing is changed, so a run can be repeated on the same data.
// Settings: BASE_URL, C4R_LOAD_COMPANY, C4R_LOAD_SESSION (loadtest/seed.py), USERS, MINUTES.
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost";
const CMP = __ENV.C4R_LOAD_COMPANY;
const USERS = Number(__ENV.USERS || 50);
const MINUTES = Number(__ENV.MINUTES || 3);

export const options = {
  scenarios: {
    staff: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "30s", target: USERS },
        { duration: `${MINUTES}m`, target: USERS },
        { duration: "15s", target: 0 },
      ],
    },
  },
  // Not a target agreed with the customer (OPEN): only a mark of a broken run.
  // The per-screen lines make k6 print the time of every screen apart ("max>=0" never fails).
  thresholds: {
    http_req_failed: ["rate<0.01"],
    ...Object.fromEntries(
      ["index", "me", "rentals", "vehicles", "clients", "orders", "payments"].map((n) => [
        `http_req_duration{name:${n}}`,
        ["max>=0"],
      ]),
    ),
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

const params = {
  headers: { Cookie: `c4r_session=${__ENV.C4R_LOAD_SESSION}` },
};

const SCREENS = [
  ["rentals", `/api/c/${CMP}/rentals`],
  ["vehicles", `/api/c/${CMP}/vehicles`],
  ["clients", `/api/c/${CMP}/clients`],
  ["orders", `/api/c/${CMP}/orders`],
  ["payments", `/api/c/${CMP}/payments`],
];

export default function () {
  const page = http.get(`${BASE}/`, { tags: { name: "index" } });
  check(page, { "index 200": (r) => r.status === 200 });
  const me = http.get(`${BASE}/api/me`, { ...params, tags: { name: "me" } });
  check(me, { "me 200": (r) => r.status === 200 });
  for (const [name, path] of SCREENS) {
    const r = http.get(`${BASE}${path}`, { ...params, tags: { name } });
    check(r, { [`${name} 200`]: (x) => x.status === 200 });
    sleep(1 + Math.random() * 2);
  }
}
