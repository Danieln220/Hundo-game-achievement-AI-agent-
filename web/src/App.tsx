import { useState } from "react";
import ProfileGate from "./components/ProfileGate";
import TrophyCase from "./components/TrophyCase";
import type { SessionResult } from "./types";

// Ambient "display case" backdrop — fixed layers lifted from the prototype:
// gold/violet/teal radial glows, two bordered rings, vertical grid lines, a faint
// noise texture, and a vignette.
function Backdrop() {
  const layer: React.CSSProperties = { position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none" };
  return (
    <>
      <div style={{ ...layer, background: "radial-gradient(1300px 760px at 50% -22%, rgba(232,179,57,.16), rgba(232,179,57,.05) 32%, transparent 56%), radial-gradient(900px 680px at 104% 116%, rgba(139,123,240,.15), transparent 58%), radial-gradient(820px 600px at -14% 108%, rgba(79,182,201,.11), transparent 58%)" }} />
      <div style={{ position: "fixed", top: -300, right: -220, width: 780, height: 780, borderRadius: "50%", border: "1px solid rgba(232,179,57,.12)", boxShadow: "0 0 0 56px rgba(232,179,57,.03), inset 0 0 140px rgba(232,179,57,.05)", zIndex: -1, pointerEvents: "none" }} />
      <div style={{ position: "fixed", bottom: -360, left: -280, width: 860, height: 860, borderRadius: "50%", border: "1px solid rgba(139,123,240,.11)", boxShadow: "inset 0 0 140px rgba(139,123,240,.05)", zIndex: -1, pointerEvents: "none" }} />
      <div style={{ ...layer, opacity: 0.6, background: "repeating-linear-gradient(90deg, rgba(255,255,255,.024) 0 1px, transparent 1px 48px)" }} />
      <div style={{ ...layer, opacity: 0.05, mixBlendMode: "overlay", backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")" }} />
      <div style={{ ...layer, background: "radial-gradient(135% 105% at 50% -8%, transparent 54%, rgba(0,0,0,.6))" }} />
    </>
  );
}

export default function App() {
  const [session, setSession] = useState<SessionResult | null>(null);
  return (
    <div className="app">
      <Backdrop />
      {session ? (
        <TrophyCase session={session} onSignOut={() => setSession(null)} />
      ) : (
        <ProfileGate onLoaded={setSession} />
      )}
    </div>
  );
}
