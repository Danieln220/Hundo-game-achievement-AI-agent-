/**
 * Offline checks for the 23.5 frontend fixes. Runs the REAL web/src modules in
 * Node (bundled with the esbuild that ships inside Vite — no new dependency),
 * with fetch/Response stubbed. Run: node eval/frontend_check.mjs
 */
import { build } from "../web/node_modules/esbuild/lib/main.js";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const results = [];
const check = (label, ok, detail = "") => {
  results.push(!!ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${!ok && detail ? `\n      ${detail}` : ""}`);
};

// ── bundle web/src/api.ts + safeUrl.ts for Node ──────────────────────────────
const dir = mkdtempSync(join(tmpdir(), "hundo-fe-"));
const entry = join(dir, "entry.ts");
writeFileSync(entry, `
export { safeUrl } from "${process.cwd()}/web/src/safeUrl";
export { askStream, apiError, ApiError, isWaking } from "${process.cwd()}/web/src/api";
`);
const out = join(dir, "bundle.mjs");
await build({
  entryPoints: [entry], outfile: out, bundle: true, format: "esm", platform: "neutral",
  define: { "import.meta.env.VITE_API_URL": '"http://api.test"' },
  logLevel: "silent",
});
const { safeUrl, askStream, apiError, ApiError } = await import("file://" + out);

// ── (e) safeUrl ──────────────────────────────────────────────────────────────
globalThis.window = { location: { origin: "https://hundoai.vercel.app" } };
for (const [url, want] of [
  ["https://steamcommunity.com/guide", "https://steamcommunity.com/guide"],
  ["http://example.com/a?b=1", "http://example.com/a?b=1"],
  ["javascript:alert(document.cookie)", null],
  ["data:text/html,<script>x</script>", null],
  ["JavaScript:alert(1)", null],
  ["", null], [null, null], ["not a url", null],
]) {
  const got = safeUrl(url);
  check(`safeUrl(${JSON.stringify(url)})`, want === null ? got === null : got === want, `got ${got}`);
}

// ── stub fetch so askStream can be driven ────────────────────────────────────
function sseResponse(frames, { status = 200, headers = {} } = {}) {
  const body = {
    getReader() {
      let i = 0;
      return { read: async () => (i < frames.length
        ? { done: false, value: new TextEncoder().encode(frames[i++]) }
        : { done: true, value: undefined }) };
    },
  };
  return { ok: status < 400, status, body, headers: { get: (k) => headers[k.toLowerCase()] ?? null } };
}
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

// (b) error body preserved, Retry-After appended
globalThis.fetch = async () => ({
  ok: false, status: 429,
  headers: { get: (k) => (k.toLowerCase() === "retry-after" ? "42" : null) },
  json: async () => ({ detail: "Rate limit reached (15 per min)." }),
});
let err = await askStream("q", "1", [], () => {}).catch((e) => e);
check("429 keeps the server's message", err instanceof ApiError && err.message.includes("Rate limit reached"), err?.message);
check("429 keeps the status + Retry-After", err.status === 429 && err.message.includes("42"), err?.message);

// (b) stream that ends with NO result event must throw, not return {}
globalThis.fetch = async () => sseResponse([frame("progress", { node: "plan" })]);
err = await askStream("q", "1", [], () => {}).catch((e) => e);
check("stream with no result throws (no hollow turn)", err instanceof ApiError, JSON.stringify(err));

// (f) a malformed frame must not kill a good answer
globalThis.fetch = async () => sseResponse([
  frame("progress", { node: "plan" }),
  "event: token\ndata: {bad json\n\n",
  frame("token", { text: "Hel" }),
  frame("token", { text: "lo" }),
  frame("result", { answer: "Hello", route: "analysis" }),
]);
const toks = [];
const nodes = [];
const res = await askStream("q", "1", [], (n) => nodes.push(n), undefined, (t) => toks.push(t));
check("malformed SSE frame is skipped, answer survives", res.answer === "Hello", JSON.stringify(res));
check("…and good token frames still arrive", toks.join("") === "Hello", toks.join(""));
check("…and progress events still arrive", nodes.join(",") === "plan", nodes.join(","));

// multi-line data frames still join per the SSE spec
globalThis.fetch = async () => sseResponse([`event: result\ndata: {"answer":\ndata: "multi"}\n\n`]);
const multi = await askStream("q", "1", [], () => {});
check("multi-line data frame parsed", multi.answer === "multi", JSON.stringify(multi));

// apiError on a non-JSON body falls back to the status line
const e2 = await apiError({ status: 500, headers: { get: () => null }, json: async () => { throw new Error("nope"); } });
check("non-JSON error body → status message", e2.message.includes("500"), e2.message);

const passed = results.filter(Boolean).length;
console.log(`\n  ${passed} passed, ${results.length - passed} failed`);
process.exit(passed === results.length ? 0 : 1);
