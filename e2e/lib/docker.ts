import { request } from "node:http";

// The Docker Engine API through the mounted socket: no Docker CLI in the browser image.
const project = process.env.COMPOSE_PROJECT ?? "c4rsys-e2e";

function call(method: string, path: string): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath: "/var/run/docker.sock", path, method }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        const body = Buffer.concat(chunks);
        if ((res.statusCode ?? 500) >= 300) reject(new Error(`${method} ${path}: ${res.statusCode} ${body}`));
        else resolve(body);
      });
    });
    req.on("error", reject);
    req.end();
  });
}

async function containerId(service: string): Promise<string> {
  const filters = JSON.stringify({
    label: [`com.docker.compose.project=${project}`, `com.docker.compose.service=${service}`],
  });
  const list = JSON.parse((await call("GET", `/containers/json?filters=${encodeURIComponent(filters)}`)).toString()) as {
    Id: string;
  }[];
  if (!list[0]) throw new Error(`No running container for ${service}`);
  return list[0].Id;
}

/** The latest code sent to `email`, from the «DEV MAIL» line of the server log. */
export async function mailCode(email: string, after = ""): Promise<string> {
  const id = await containerId("backend");
  const pattern = new RegExp(`DEV MAIL to ${email.replace(/[.+]/g, "\\$&")}: \\S+ code (\\S+)`, "g");
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const log = (await call("GET", `/containers/${id}/logs?stdout=1&stderr=1&tail=500`)).toString("latin1");
    const codes = [...log.matchAll(pattern)].map((m) => m[1]).filter((code): code is string => Boolean(code));
    const code = codes[codes.length - 1];
    if (code && code !== after) return code;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`No mail code for ${email}`);
}

export async function restart(service: string): Promise<void> {
  await call("POST", `/containers/${await containerId(service)}/restart?t=10`);
}
