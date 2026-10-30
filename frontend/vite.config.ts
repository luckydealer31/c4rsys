import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

/** A short stable hash of the text (FNV-1a), the version of the cached shell. */
function hash(text: string): string {
  let value = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value.toString(16).padStart(8, "0");
}

/** The service worker of the build (ТЗ § 10.4.3): sw-template.js with the list of the built
 * files and a version that changes with them. Only `vite build` emits it; the development
 * server has no service worker. */
function serviceWorker(): Plugin {
  return {
    name: "c4r-service-worker",
    apply: "build",
    async generateBundle(_options, bundle) {
      const files = Object.keys(bundle)
        .filter((name) => name.startsWith("assets/") && !name.endsWith(".map"))
        .sort();
      const version = hash(files.join("\n"));
      // Node's file system, without its type package in this project.
      const fs = (await import(/* @vite-ignore */ `node:${"fs/promises"}`)) as {
        readFile: (path: URL, encoding: "utf8") => Promise<string>;
      };
      const source = (await fs.readFile(new URL("./sw-template.js", import.meta.url), "utf8"))
        .replace("__C4R_PRECACHE__", JSON.stringify(files))
        .replace("__C4R_VERSION__", JSON.stringify(version));
      this.emitFile({ type: "asset", fileName: "sw.js", source });
    },
  };
}

export default defineConfig(({ mode }) => {
  // loadEnv also reads VITE_* variables from the process environment (docker-compose sets it).
  const env = loadEnv(mode, ".");
  const apiTarget = env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

  return {
    plugins: [react(), serviceWorker()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target: apiTarget, changeOrigin: false },
      },
    },
    preview: {
      proxy: {
        "/api": { target: apiTarget, changeOrigin: false },
      },
    },
  };
});
