"""Streamlit UI — input/output only, no agent logic.
Resolves the visitor's Steam profile, makes sure a snapshot exists, then calls
agent.run() and renders: answer, chart, and reasoning trace.
Keep this file thin. If agent logic creeps in, move it to agent/."""
import streamlit as st

from agent import run, make_chart
from config import missing_secrets
from data_layer.resolver import resolve_steam_id, SteamResolveError
from data_layer.snapshot import (
    build_snapshot,
    clear_snapshot,
    has_snapshot,
    snapshot_age_days,
    PrivateProfileError,
)


def _fetch_with_progress(steam_id: str, force: bool = False) -> None:
    """Build (or rebuild) a user's snapshot with a live Streamlit progress bar.
    `force=True` clears the existing snapshot first (a manual refresh)."""
    if force:
        clear_snapshot(steam_id)
    if force or not has_snapshot(steam_id):
        st.caption("Fetching your Steam library...")
        bar = st.progress(0.0)
        status = st.empty()

        def _on_progress(done, total):
            bar.progress(done / total)
            status.caption(f"Fetched {done}/{total} data points")

        build_snapshot(steam_id, progress_cb=_on_progress)
        bar.empty()
        status.empty()

st.set_page_config(page_title="Hundo — Steam Achievement Analyst", page_icon="🎮")
st.title("Hundo — Steam Achievement Analyst")
st.caption("Ask anything about your Steam achievements.")

# ── Startup secret check ──────────────────────────────────────────────────────
_missing = missing_secrets()
if _missing:
    st.error(f"Missing required secrets: {', '.join(_missing)}. Set them in your .env.")
    st.stop()

# ── Profile selection (sidebar) ───────────────────────────────────────────────
with st.sidebar:
    st.header("Your Steam profile")
    st.caption(
        "Enter your Steam **alias** (custom URL name), your SteamID, or your "
        "profile link. Your profile's game details must be **Public**."
    )
    user_input = st.text_input(
        "Steam alias, ID, or URL",
        placeholder="e.g. daniel",
    )
    with st.expander("Where do I find this?"):
        st.markdown(
            "- **Alias** = the custom name in your profile URL: "
            "`steamcommunity.com/id/`**`daniel`** → just type `daniel`.\n"
            "- No custom URL? Paste the full link (works with "
            "`/id/...` or `/profiles/7656...`) or your 17-digit SteamID.\n"
            "- Note: your in-game **display name** can't be searched — use the "
            "alias from your profile URL instead."
        )
    load_clicked = st.button("Load my data", type="primary")

    if load_clicked and user_input:
        try:
            with st.spinner("Resolving profile..."):
                steam_id = resolve_steam_id(user_input)
            _fetch_with_progress(steam_id)
            st.session_state["steam_id"] = steam_id
            st.session_state["chat"] = []   # fresh conversation per profile
            st.success(f"Loaded profile {steam_id}.")
        except SteamResolveError as e:
            st.error(str(e))
        except PrivateProfileError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Couldn't load that profile: {e}")

    active_id = st.session_state.get("steam_id")
    if active_id:
        st.info(f"Active profile: {active_id}")

        age = snapshot_age_days(active_id)
        if age is not None:
            st.caption(f"Data is {age:.1f} day(s) old.")

        if st.button("🔄 Refresh my data"):
            try:
                _fetch_with_progress(active_id, force=True)
                st.success("Data refreshed.")
            except PrivateProfileError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Couldn't refresh: {e}")

# ── Chat ──────────────────────────────────────────────────────────────────────
active_id = st.session_state.get("steam_id")
if not active_id:
    st.info("👈 Enter your Steam profile in the sidebar to get started.")
    st.stop()

st.session_state.setdefault("chat", [])


def _render_trace(result: dict) -> None:
    with st.expander("Reasoning trace", expanded=False):
        plan = result.get("plan", "")
        if plan:
            st.subheader("Plan")
            st.markdown(plan)
        if result.get("interpretation"):
            st.caption(f"Interpretation: {result['interpretation']}")

        code_history = result.get("code_history") or []
        if code_history:
            st.subheader("Code attempts")
            for i, code in enumerate(code_history, 1):
                st.code(code, language="python", line_numbers=True)
                if i < len(code_history):
                    st.caption(f"↑ Attempt {i} not accepted — retried")

        retries = result.get("retries", 0)
        last_error = result.get("last_error")
        st.subheader("Stats")
        st.write(f"**Code attempts:** {retries}")
        if last_error and not (result.get("answer") or "").startswith("I couldn"):
            st.warning(f"Recovered from error: {last_error}")
        elif last_error:
            st.error(f"Final error: {last_error}")

        raw = result.get("last_result")
        if raw:
            st.write("**Raw computed result:**")
            st.code(raw)


def _render_extras(result: dict, chart_path) -> None:
    """Insight + chart + trace, shared by live and replayed turns."""
    if result.get("insight"):
        st.info(f"💡 {result['insight']}")
    if chart_path:
        st.image(chart_path)
    if result.get("plan") or result.get("code_history"):
        _render_trace(result)


# Replay the conversation so far (stored chart paths — no regeneration).
for turn in st.session_state["chat"]:
    st.chat_message("user").write(turn["question"])
    with st.chat_message("assistant"):
        st.write(turn["result"].get("answer", "(no answer)"))
        _render_extras(turn["result"], turn.get("chart_path"))

question = st.chat_input("e.g. Which of my games am I closest to 100% on?")

if question:
    st.chat_message("user").write(question)

    with st.chat_message("assistant"):
        history = [
            {"question": t["question"], "answer": t["result"].get("answer", "")}
            for t in st.session_state["chat"]
        ]
        with st.spinner("Thinking..."):
            result = run(question, steam_id=active_id, history=history, with_insight=True)

        # Answer first (perceived speed), then the slower chart in a second pass.
        st.write(result.get("answer", "(no answer)"))

        chart_path = result.get("chart_path")   # some nodes (e.g. audit) make their own
        if not chart_path and result.get("chart_pending"):
            with st.spinner("Generating chart..."):
                chart_path = make_chart(result)

        _render_extras(result, chart_path)

    st.session_state["chat"].append(
        {"question": question, "result": result, "chart_path": chart_path}
    )
