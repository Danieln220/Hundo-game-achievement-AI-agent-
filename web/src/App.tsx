import { useState } from "react";
import ProfileGate from "./components/ProfileGate";
import ProfileHeader from "./components/ProfileHeader";
import Chat from "./components/Chat";
import type { SessionResult } from "./types";

export default function App() {
  const [session, setSession] = useState<SessionResult | null>(null);

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">🎮</span>
          <div>
            <h1>Hundo</h1>
            <span className="tagline">Steam Achievement Analyst</span>
          </div>
        </div>
        {session && (
          <button className="switch" onClick={() => setSession(null)}>
            Switch profile
          </button>
        )}
      </header>

      {session ? (
        <>
          <ProfileHeader session={session} />
          <Chat steamId={session.steam_id} games={session.games} />
        </>
      ) : (
        <ProfileGate onLoaded={setSession} />
      )}
    </div>
  );
}
