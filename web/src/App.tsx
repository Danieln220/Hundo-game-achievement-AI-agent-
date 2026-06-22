import { useState } from "react";
import ProfileGate from "./components/ProfileGate";
import Chat from "./components/Chat";

export default function App() {
  const [steamId, setSteamId] = useState<string | null>(null);
  const [games, setGames] = useState(0);

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
        {steamId && (
          <button className="switch" onClick={() => setSteamId(null)}>
            Switch profile
          </button>
        )}
      </header>

      {steamId ? (
        <Chat steamId={steamId} games={games} />
      ) : (
        <ProfileGate
          onLoaded={(id, g) => {
            setSteamId(id);
            setGames(g);
          }}
        />
      )}
    </div>
  );
}
