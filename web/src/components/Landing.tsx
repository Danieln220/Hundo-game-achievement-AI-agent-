import { useEffect, useRef, useState } from "react";
import ProfileGate from "./ProfileGate";
import { DEMO_PROFILE, demoPlan, health, steamLoginUrl, withWake, type DemoPlan } from "../api";
import { tierOf, pctLabel } from "../tcTheme";
import type { SessionResult } from "../types";

// The first screen for someone who has never used Hundo. The thesis is the
// coach, not the collection: the hero object is the agent's PLAN for a game,
// the sections are three real answers, the proof of how answers are made,
// and the case as the reward. Everything below is real demo-library data,
// frozen here on purpose (a static hero never waits on a cold start).

// ── Static demo data (from the public demo library, 2026-09-23) ──────────────
// Frozen copy of /demo/plan: the hero paints from this instantly and swaps in
// the live answer when the API responds (a sleeping server costs nothing).
const PLAN: DemoPlan = {
  game: "Dragon Ball Z: Kakarot", unlocked: 33, total: 42, pct: 78.6, left: 9,
  locked: [
    { name: "Getting Greedy", desc: "Summon Shenron 5 times from the Dragon Ball menu to make a wish.", pct: 19.8, hidden: false },
    { name: "Z Combo Zealot", desc: "Perform 10 Z Combos.", pct: 18.4, hidden: false },
    { name: "Who Needs a Phone?", desc: "Receive 20 telepathic messages from King Kai.", pct: 17.5, hidden: false },
    { name: "Can't Touch This", desc: "Get 50 instant victories on enemies.", pct: 17.1, hidden: false },
    { name: "Demolition Artist", desc: "", pct: 10.7, hidden: true },
    { name: "Shenron's Favorite", desc: "Summon Shenron 10 times from the Dragon Ball menu to make a wish.", pct: 10.4, hidden: false },
    { name: "Tough Enough", desc: "", pct: 9.9, hidden: true },
    { name: "Only the Finest", desc: "Make 5 full-course meals.", pct: 8.5, hidden: false },
  ],
  meta: { name: "Dragon Ball Master", desc: "Obtain all achievements.", pct: 5.3, hidden: false },
};
const QUICK_N = 3;
// Steam ships some titles in ALL CAPS ("DRAGON BALL Z: KAKAROT"); on a landing
// page that reads as shouting, so title-case those — and only those.
const SMALL = new Set(["of", "the", "and", "a", "an", "in", "on", "to", "for", "vs"]);
function niceName(n: string): string {
  if (!/[A-Z]/.test(n) || n !== n.toUpperCase()) return n;
  return n.toLowerCase().replace(/\b([a-z])([a-z']*)/g, (m, f, r, i) => (i > 0 && SMALL.has(m)) ? m : f.toUpperCase() + r);
}
function agoLabel(builtAt?: number | null): string | null {
  if (!builtAt) return null;
  const h = (Date.now() / 1000 - builtAt) / 3600;
  if (h < 1) return "updated just now";
  if (h < 48) return `updated ${Math.round(h)}h ago`;
  return `updated ${Math.round(h / 24)}d ago`;
}
const RAREST = [
  { name: "Finnish Ace", game: "War Thunder", pct: "1.3%" },
  { name: "Hatch Me If You Can", game: "Forza Horizon 4", pct: "1.6%" },
  { name: "Clipping Zone Master", game: "CarX Drift Racing Online", pct: "1.9%" },
];
const STALLED = [
  ["Rocket League", "75.0%"], ["Cloudpunk", "48.1%"], ["Forza Horizon 5", "37.2%"], ["Company of Heroes 2", "16.2%"],
];
const TIERS: [string, string, number, number][] = [
  ["Common", "common", 354, 100], ["Uncommon", "uncommon", 257, 73], ["Rare", "rare", 45, 13], ["Ultra", "ultra", 35, 10],
];
const NEXT: [string, string, string, string][] = [
  ["Planned", "planned", "In-game overlay", "What's worth chasing this session, and how to get it — inside the game, without alt-tabbing."],
  ["Planned", "planned", "Xbox achievements", "Your Xbox library in the same plan, case and curator."],
  ["Prototype", "proto", "PlayStation trophies", "Your trophies in the same plan, Bronze to Platinum."],
];

// Animation delay as a CSS custom property (the keyframes live in index.css).
const d = (s: number): React.CSSProperties => ({ ["--d" as string]: `${s}s` } as React.CSSProperties);

// ── The rest of the case: a field of card sleeves in depth behind the hero ───
// Three parallax layers, ~11% faintly lit in the real tier mix, one empty
// sleeve pulled gold every few seconds (at most three stay lit). Pure canvas,
// no dependency; returns a stop function.
function startCaseField(canvas: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return () => {};
  const W = canvas.width, H = canvas.height;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const s = Math.min(1, Math.max(0.55, W / 1440));
  const TIER: [number, number, number][] = [[126, 138, 160], [79, 182, 201], [139, 123, 240], [240, 192, 74]];
  const rgba = (c: [number, number, number], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;
  const rand = (i: number) => { const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); };
  type Cell = { r: number; c: number; tier: number; glow: number };
  type Layer = { cw: number; ch: number; gap: number; speed: number; par: number; alpha: number; lit: number; stepX: number; stepY: number; rows: number; span: number; cells: Cell[] };
  const layers: Layer[] = ([
    { cw: 44 * s, ch: 58 * s, gap: 12 * s, speed: 4, par: 6, alpha: 0.17, lit: 0.10 },
    { cw: 72 * s, ch: 96 * s, gap: 18 * s, speed: 7, par: 12, alpha: 0.3, lit: 0.11 },
    { cw: 108 * s, ch: 144 * s, gap: 26 * s, speed: 11, par: 22, alpha: 0.47, lit: 0.12 },
  ] as Omit<Layer, "stepX" | "stepY" | "rows" | "span" | "cells">[]).map((L, li) => {
    const stepX = L.cw + L.gap, stepY = L.ch + L.gap;
    const cols = Math.ceil(W / stepX) + 2, rows = Math.ceil(H / stepY) + 2;
    const cells: Cell[] = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const i = li * 100000 + r * 1000 + c;
      const u = rand(i), v = rand(i + 0.5);
      const tier = u < L.lit ? (v < 0.51 ? 0 : v < 0.88 ? 1 : v < 0.95 ? 2 : 3) : -1;
      cells.push({ r, c, tier, glow: 0 });
    }
    return { ...L, stepX, stepY, rows, span: rows * stepY, cells };
  });
  let mx = 0.5, my = 0.5, tx = 0.5, ty = 0.5;
  const onMove = (e: PointerEvent) => { tx = e.clientX / window.innerWidth; ty = e.clientY / window.innerHeight; };
  window.addEventListener("pointermove", onMove, { passive: true });
  let last = performance.now(), t = 0, nextPull = 2600, raf = 0, stopped = false;
  const near = layers[2], pulled: Cell[] = [];
  function rrect(x: number, y: number, w: number, h: number, r: number) {
    ctx!.beginPath(); ctx!.moveTo(x + r, y);
    ctx!.arcTo(x + w, y, x + w, y + h, r); ctx!.arcTo(x + w, y + h, x, y + h, r);
    ctx!.arcTo(x, y + h, x, y, r); ctx!.arcTo(x, y, x + w, y, r); ctx!.closePath();
  }
  function cellPos(L: Layer, cell: Cell, drift: number, px: number, py: number): [number, number] {
    const x = cell.c * L.stepX - L.stepX + (cell.r % 2 ? L.stepX / 2 : 0) + px;
    const y0 = cell.r * L.stepY - L.stepY + py - drift;
    return [x, ((y0 % L.span) + L.span) % L.span - L.stepY];
  }
  function frame(now: number) {
    if (stopped) return;
    const dt = Math.min(50, now - last); last = now; t += dt;
    mx += (tx - mx) * 0.04; my += (ty - my) * 0.04;
    ctx!.clearRect(0, 0, W, H);
    ctx!.lineWidth = 1;
    for (const L of layers) {
      const drift = reduce ? 0 : (t / 1000) * L.speed;
      const px = (mx - 0.5) * -L.par, py = (my - 0.5) * -L.par;
      for (const cell of L.cells) {
        const [x, y] = cellPos(L, cell, drift, px, py);
        if (y > H || y + L.ch < 0 || x > W || x + L.cw < 0) continue;
        const u = Math.min(1, Math.max(0, (x / W - 0.28) / 0.45));   // nearly empty behind the copy
        const xf = 0.04 + 0.96 * u * u * (3 - 2 * u);
        const yf = 1 - Math.max(0, (y / H - 0.5) / 0.5);              // fades out toward the bottom
        const a = L.alpha * xf * yf;
        if (a <= 0.01) continue;
        rrect(x, y, L.cw, L.ch, Math.max(3, L.cw * 0.06));
        if (cell.tier < 0) { ctx!.strokeStyle = rgba(TIER[0], 0.1 * a); ctx!.stroke(); continue; }
        const col = TIER[cell.tier], ultra = cell.tier === 3;
        if (cell.glow > 0.02) {
          ctx!.save(); ctx!.shadowColor = rgba(TIER[3], 0.9 * cell.glow); ctx!.shadowBlur = 48 * cell.glow;
          ctx!.fillStyle = rgba(TIER[3], (0.12 + 0.5 * cell.glow) * a); ctx!.fill(); ctx!.restore();
          cell.glow *= Math.pow(0.985, dt / 16);
        } else { ctx!.fillStyle = rgba(col, (ultra ? 0.1 : 0.045) * a); ctx!.fill(); }
        ctx!.strokeStyle = rgba(col, (ultra ? 0.45 : 0.22) * a); ctx!.stroke();
      }
    }
    if (!reduce && t > nextPull) {                                     // a pull: one empty near sleeve lights up
      nextPull = t + 3200 + Math.random() * 2400;
      const drift = (t / 1000) * near.speed, px = (mx - 0.5) * -near.par, py = (my - 0.5) * -near.par;
      const pool = near.cells.filter((c) => {
        const [qx, qy] = cellPos(near, c, drift, px, py);
        return c.tier < 0 && qx > W * 0.35 && qx + near.cw < W && qy > 100 && qy + near.ch < H * 0.55;
      });
      if (pool.length) {
        const pick = pool[Math.floor(Math.random() * pool.length)]; pick.tier = 3; pick.glow = 1; pulled.push(pick);
        if (pulled.length > 3) { const old = pulled.shift()!; old.tier = -1; old.glow = 0; }
      }
    }
    if (!reduce) raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
  return () => { stopped = true; cancelAnimationFrame(raf); window.removeEventListener("pointermove", onMove); };
}

export default function Landing({ onLoaded }: { onLoaded: (s: SessionResult, question?: string) => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Sections below the fold hold their animations until they scroll into view.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const targets = root.querySelectorAll<HTMLElement>("[data-reveal]");
    if (!("IntersectionObserver" in window)) { targets.forEach((t) => t.setAttribute("data-reveal", "on")); return; }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.setAttribute("data-reveal", "on"); io.unobserve(e.target); } });
    }, { threshold: 0.18 });
    targets.forEach((t) => io.observe(t));
    return () => io.disconnect();
  }, []);

  // The sleeve field sizes itself to the page and restarts on resize.
  useEffect(() => {
    const canvas = canvasRef.current, root = rootRef.current;
    if (!canvas || !root) return;
    let stop = () => {};
    let timer = 0;
    const start = () => {
      stop();
      canvas.width = Math.max(320, root.clientWidth);
      canvas.height = Math.min(1040, Math.round(canvas.width * 0.72) + 300);
      stop = startCaseField(canvas);
    };
    start();
    const onResize = () => { window.clearTimeout(timer); timer = window.setTimeout(start, 200); };
    window.addEventListener("resize", onResize);
    return () => { stop(); window.clearTimeout(timer); window.removeEventListener("resize", onResize); };
  }, []);

  // The ONE /health probe for the page: it warms the free-tier server while the
  // visitor reads, and says whether the demo is configured. The demo controls
  // show from first paint (a cold start must not hide the primary CTA) and only
  // disappear on an explicit demo=false; withWake rides out the cold start so a
  // single failed request doesn't decide it for the whole visit. If the demo is
  // off, a click still gets a clear message from /session instead of a profile.
  const [demoOn, setDemoOn] = useState(true);
  useEffect(() => {
    let alive = true;
    withWake(() => health(), undefined, () => !alive)
      .then((h) => { if (alive && h) setDemoOn(!!h.demo); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  // Stale-while-revalidate for the hero: the frozen plan paints at once; the
  // live one replaces it in place when the API answers. A failure changes nothing.
  const [plan, setPlan] = useState<DemoPlan>(PLAN);
  useEffect(() => {
    let alive = true;
    demoPlan().then((p) => { if (alive && p && p.locked?.length) setPlan(p); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const quick = plan.locked.slice(0, QUICK_N), grind = plan.locked.slice(QUICK_N);
  const updated = agoLabel(plan.built_at);
  // "Get the guide" / the example questions enter the demo with that question
  // pre-filled. The question rides INSIDE the request, so the gate hands it back
  // only with the demo load that request started — never with another profile.
  const [request, setRequest] = useState<{ profile: string; nonce: number; question: string } | undefined>();
  function askDemo(q: string) {
    setRequest((r) => ({ profile: DEMO_PROFILE, nonce: (r?.nonce ?? 0) + 1, question: q }));
    document.getElementById("top")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const AskLink = ({ q, children }: { q: string; children: React.ReactNode }) =>
    demoOn ? <button className="landing-ask" onClick={() => askDemo(q)}>{children}</button> : <span className="landing-ask landing-ask-off">{children}</span>;

  return (
    <div className="landing" ref={rootRef}>
      <canvas ref={canvasRef} className="landing-field" aria-hidden="true" />

      <nav className="landing-nav">
        <a href="#top" className="landing-brand">
          <span className="landing-mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#262c3d" strokeWidth="3" /><circle cx="12" cy="12" r="9" stroke="#e8b339" strokeWidth="3" strokeLinecap="round" strokeDasharray="56.5" strokeDashoffset="15" transform="rotate(-90 12 12)" /></svg>
            <span>H</span>
          </span>
          <span className="landing-brand-text"><b>Hundo</b><small>Achievement coach for Steam</small></span>
        </a>
        <div className="landing-nav-links">
          <a href="#help">How it helps</a>
          <a href="#how">Why trust it</a>
          <a href="#next">What's next</a>
          <a className="landing-nav-steam" href={steamLoginUrl()}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><circle cx="15" cy="9.5" r="2.4" /><circle cx="8.5" cy="15.5" r="2" /><path d="M10.2 14.2 13.2 11" /></svg>
            Sign in through Steam
          </a>
        </div>
      </nav>

      {/* HERO: the offer, and the agent's actual output as the object */}
      <header id="top" className="landing-hero">
        <div className="landing-copy">
          <span className="landing-eyebrow lr" style={d(0.05)}>For the game you're playing right now</span>
          <h1 className="landing-h1 lr" style={d(0.18)}>Know your <em>next</em><br />achievement —<br />and how to get it.</h1>
          <p className="landing-sub lr" style={d(0.3)}>Hundo reads your public Steam library, works out what's left in each game, orders it easiest-first, and pulls the guide when you're stuck. Ask it anything — it computes the answer from your own data instead of guessing.</p>
          <div className="landing-gate lr" style={d(0.42)}>
            <ProfileGate onLoaded={onLoaded} demoOn={demoOn} request={request} />
          </div>
        </div>

        <div className="landing-plan-stage">
          <div className="landing-plan-shadow" aria-hidden="true" />
          <div className="landing-plan">
            <span className="landing-sheen" aria-hidden="true" />
            <div className="landing-plan-head">
              <span className="landing-label">Your next plan</span>
              <span className="landing-plan-meta">from the demo library{updated ? ` · ${updated}` : ""}</span>
            </div>
            <div className="landing-plan-game">
              <div className="landing-plan-title"><b>{niceName(plan.game)}</b><span>{plan.unlocked} of {plan.total} · {plan.left} left</span></div>
              <span className="landing-bar"><span className="landing-bar-fill" style={{ width: `${plan.pct}%`, ...d(1.2) }} /></span>
              <span className="landing-plan-meta">{plan.pct}% — the closest game to 100% in this library</span>
            </div>
            <span className="landing-label landing-label-quick">Quick wins · chase these first</span>
            {quick.map((a, i) => (
              <div key={a.name} className="landing-plan-row lr" style={d(1.3 + i * 0.15)}>
                <span className="landing-check" aria-hidden="true" />
                <span className="landing-plan-text"><b>{a.name}</b><small>{a.desc || (a.hidden ? "Hidden — the guide reveals the steps." : "")}</small></span>
                <span className="landing-plan-side">
                  <span className={`landing-chip landing-chip-${tierOf(a.pct)}`}>{pctLabel(a.pct)}</span>
                  <AskLink q={`How do I unlock "${a.name}" in ${plan.game}?`}>Get the guide</AskLink>
                </span>
              </div>
            ))}
            <span className="landing-label landing-label-grind lr" style={d(1.8)}>Then the grind</span>
            <div className="landing-plan-grind lr" style={d(1.9)}>
              {grind.map((g) => (
                <span key={g.name} className={g.hidden ? "muted" : ""}><span>{g.name} <i>· {g.hidden ? "hidden — the guide reveals it" : g.desc.replace(/\.$/, "")}</i></span><span className={`landing-t-${tierOf(g.pct)}`}>{pctLabel(g.pct)}</span></span>
              ))}
              {plan.meta && (
                <span className="muted"><span>{plan.meta.name} <i>· unlocks with the rest</i></span><span className={`landing-t-${tierOf(plan.meta.pct)}`}>{pctLabel(plan.meta.pct)}</span></span>
              )}
            </div>
            <div className="landing-plan-foot"><span>easiest first · by how many players have each one</span><span>verified against your library</span></div>
          </div>
        </div>
      </header>

      {/* HOW IT HELPS: three real answers */}
      <section id="help" className="landing-section" data-reveal="">
        <span className="landing-label lr" style={d(0.05)}>01 · How it helps</span>
        <h2 className="landing-h2 lr" style={d(0.15)}>Ask it like you'd ask a friend who has your save file.</h2>
        <p className="landing-lead lr" style={d(0.25)}>Three things people ask most. Every answer below is real — it came out of the demo library.</p>
        <div className="landing-cards">
          <article className="landing-card lr" style={d(0.3)}>
            <span className="landing-label">What next</span>
            <h3>"What should I chase tonight?"</h3>
            <p>It looks across every game you own and picks what's closest, easiest, or rarest — whichever you ask for.</p>
            <div className="landing-term">
              <span className="q hd-type" style={d(0.7)}>&gt; what am I closest to finishing?</span>
              <span className="a lr" style={d(1.9)}>Dragon Ball Z: Kakarot — 9 achievements left (78.6%)</span>
              <span className="q hd-type" style={{ ...d(2.5), ["--steps" as string]: 30 } as React.CSSProperties}>&gt; and the easiest one anywhere?</span>
              <span className="a lr" style={d(3.6)}>Elite Slayer · Risk of Rain 2 · 90.7% of players have it</span>
            </div>
            <AskLink q="What am I closest to finishing?">Ask it yourself →</AskLink>
          </article>
          <article className="landing-card lr" style={d(0.42)}>
            <span className="landing-label">Where you stalled</span>
            <h3>"Which games did I leave half-done?"</h3>
            <p>The ones you stopped playing with real progress on the board — and a roadmap to pick any of them back up.</p>
            <div className="landing-rows">
              {STALLED.map(([g, p]) => <span key={g}><b>{g}</b><span>{p}</span></span>)}
            </div>
            <AskLink q="Which games did I leave half-done?">Ask it yourself →</AskLink>
          </article>
          <article className="landing-card lr" style={d(0.54)}>
            <span className="landing-label">When you're stuck</span>
            <h3>"How do I unlock this one?"</h3>
            <p>It searches the guides for you and answers with numbered sources — inside the plan, without alt-tabbing.</p>
            <div className="landing-term">
              <span className="q">&gt; how do I get Only the Finest?</span>
              <span className="a">Only the Finest · 8.5% · "Make 5 full-course meals."</span>
              <span className="s"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#5be0d0" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>guide found — steps with 3 sources</span>
            </div>
            <AskLink q={`How do I unlock "Only the Finest" in Dragon Ball Z: Kakarot?`}>Ask it yourself →</AskLink>
          </article>
        </div>
      </section>

      {/* WHY TRUST IT: the trace */}
      <section id="how" className="landing-section" data-reveal="">
        <span className="landing-label lr" style={d(0.05)}>02 · Why you can trust the plan</span>
        <h2 className="landing-h2 lr" style={d(0.15)}>It doesn't remember your library. It computes it.</h2>
        <p className="landing-lead">Every question becomes a small analysis run against your own data, checked before it's shown. This one is real — it came out of the demo library exactly like this.</p>
        <div className="landing-trace">
          <div className="landing-trace-steps">
            <div className="lr" style={d(0.1)}><span>01</span><span>Question</span><span className="ink">what are my 3 rarest achievements?</span></div>
            <div className="lr" style={d(0.45)}><span>02</span><span>Plan</span><span>route: rarest · scope: whole library · count: 3</span></div>
            <div className="lr top" style={d(0.8)}><span>03</span><span>Run</span>
              <span><span>pandas over 6,318 rows, in a sandbox</span>
                <pre>{`unlocked = player_unlocks[player_unlocks.achieved]\nmine = achievements.merge(unlocked, on=["appid", "api_name"])\nmine.nsmallest(3, "rarity_pct")[["display_name", "rarity_pct"]]`}</pre>
              </span>
            </div>
            <div className="lr last" style={d(1.3)}><span>04</span><span>Check</span>
              <span className="ok"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5be0d0" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 12 5 5L20 7" strokeDasharray="30" className="hd-draw" style={d(1.6)} /></svg>result verified against the snapshot · 3 rows · no retry needed</span>
            </div>
          </div>
          <div className="landing-trace-rail lr" style={d(0.05)}>
            <div><b>~50 ms</b><span>for the common questions — closest, easiest, rarest — answered straight from your library, no model in the loop.</span></div>
            <div><b>Public data</b><span>the same profile anyone can open. No password, no account to create, nothing installed.</span></div>
            <div><b>Shows its work</b><span>every answer carries its trace — plan, code, check — so you can see how it was worked out.</span></div>
          </div>
        </div>
        <div className="landing-answers">
          {RAREST.map((r, i) => (
            <div key={r.name} className={`landing-answer lr${i === 0 ? " first" : ""}`} style={d(1.8 + i * 0.15)}>
              <span className="landing-chip landing-chip-ultra">{r.pct}</span>
              <span><b>{r.name}</b><small>{r.game}</small></span>
            </div>
          ))}
        </div>
      </section>

      {/* THE CASE: the reward, kept */}
      <section id="case" className="landing-section landing-case" data-reveal="">
        <div>
          <span className="landing-label lr" style={d(0.05)}>03 · Everything you earn is kept</span>
          <h2 className="landing-h2 lr" style={d(0.15)}>The case fills as you go.</h2>
          <p className="landing-lead lr" style={d(0.25)}>Every unlock lands in your case as a card, sorted by how many players share it — rarest up front. It's the scoreboard for the plan, not the point of it.</p>
          <div className="landing-tiers lr" style={d(0.35)}>
            {TIERS.map(([name, key, n, w], i) => (
              <div key={key}><span className={`landing-t-${key}`}>{name}</span><span className="landing-bar"><span className={`landing-bar-fill landing-bg-${key}`} style={{ width: `${w}%`, ...d(0.4 + i * 0.1) }} /></span><span>{n}</span></div>
            ))}
            <small>691 unlocked cards in the demo case, by tier · 6,318 in the library</small>
          </div>
        </div>
        <div className="landing-card-stage lr" style={d(0.3)}>
          <div className="landing-sleeve" style={{ transform: "rotate(-6deg) translate(-24px,12px)" }} />
          <div className="landing-sleeve" style={{ transform: "rotate(4deg) translate(20px,8px)" }} />
          <div className="landing-pull">
            <span className="landing-sheen" aria-hidden="true" />
            <div className="landing-pull-top"><span>RAREST PULL</span><span>SLEEVED</span></div>
            <div className="landing-pull-num"><em>1.3%</em><span>of players have it</span></div>
            <div className="landing-pull-name"><b>Finnish Ace</b><span>War Thunder</span></div>
            <span className="landing-pull-foot">1 of 6,318 cards in this case</span>
          </div>
        </div>
      </section>

      {/* WHAT'S NEXT */}
      <section id="next" className="landing-section" data-reveal="">
        <span className="landing-label lr" style={d(0.05)}>04 · What's next</span>
        <h2 className="landing-h2 lr" style={d(0.15)}>Steam today. More places to get help next.</h2>
        <p className="landing-lead">More ways in, and more libraries to read.</p>
        <div className="landing-next">
          {NEXT.map(([status, cls, title, body], i) => (
            <div key={title} className="lr" style={d(0.25 + i * 0.15)}>
              <span className={`landing-status landing-status-${cls}`}>{status}</span>
              <b>{title}</b>
              <span>{body}</span>
            </div>
          ))}
        </div>
        <small className="landing-fine">Steam is the only platform live today. Nothing above is available yet.</small>
      </section>

      {/* CLOSING */}
      <section id="demo" className="landing-section landing-close" data-reveal="">
        <div className="landing-close-panel lr" style={d(0.1)}>
          <div>
            <h2 className="landing-h2">Get a plan for the game you're playing.</h2>
            <p className="landing-lead">Try it first on the demo — a real Steam library with the owner's identity removed, 78 games and 6,318 achievements. Ask it what to chase. Then sign in and get yours.</p>
          </div>
          <div className="landing-close-actions">
            {demoOn && <button className="landing-cta" onClick={() => askDemo("What should I chase next?")}>Ask it what to chase next <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></svg></button>}
            <a className="landing-cta-ghost" href={steamLoginUrl()}>Sign in through Steam</a>
          </div>
        </div>
        <footer className="landing-foot">
          <span>Hundo · not associated with Valve Corp.</span>
          <span>Steam only for now · nothing to install · your game details must be set to Public</span>
        </footer>
      </section>
    </div>
  );
}
