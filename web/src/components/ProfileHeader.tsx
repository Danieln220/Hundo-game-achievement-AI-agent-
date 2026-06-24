import type { SessionResult } from "../types";
import CompletionRing from "./CompletionRing";

export default function ProfileHeader({ session }: { session: SessionResult }) {
  const pct = session.total
    ? Math.round((session.unlocked / session.total) * 100)
    : 0;

  return (
    <div className="profile-header">
      {session.avatar && <img className="avatar" src={session.avatar} alt="" />}
      <div className="profile-info">
        <div className="profile-name">{session.persona || session.steam_id}</div>
        <div className="profile-stats">
          <span>
            <b>{session.unlocked.toLocaleString()}</b> achievements
          </span>
          <span>
            <b>{session.perfect}</b> perfect
          </span>
          <span>
            <b>{session.games}</b> games
          </span>
        </div>
      </div>
      <CompletionRing pct={pct} label="overall" />
    </div>
  );
}
