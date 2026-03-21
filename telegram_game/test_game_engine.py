from pathlib import Path
import tempfile

from telegram_game.game_engine import (
    accept_mission,
    auto_cast,
    bench_summary,
    current_team_summary,
    ensure_mission,
    load_state,
    new_game,
    next_day,
    resolve_submission,
    save_state,
)


def test_generate_and_autocast():
    state = new_game(123, "Test Studio")
    mission = ensure_mission(state)
    assert mission.code
    assert mission.roles
    picks = auto_cast(state)
    assert "translator" in picks
    assert len(mission.assigned_roles) == len(mission.roles)


def test_submit_flow_passes_after_accept_and_autocast():
    state = new_game(123, "Test Studio")
    accept_mission(state)
    auto_cast(state)
    result = resolve_submission(state)
    assert "passed" in result
    assert result["reward"] >= 15
    assert state.current_mission is None


def test_next_day_recovers_energy():
    state = new_game(123, "Test Studio")
    accept_mission(state)
    auto_cast(state)
    resolve_submission(state)
    min_energy_before = min(s.energy for s in state.roster)
    next_day(state)
    min_energy_after = min(s.energy for s in state.roster)
    assert min_energy_after >= min_energy_before


def test_save_and_load_roundtrip():
    state = new_game(456, "Roundtrip Studio")
    accept_mission(state)
    auto_cast(state)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "save.json"
        save_state(state, path)
        loaded = load_state(path)
    assert loaded is not None
    assert loaded.user_id == state.user_id
    assert loaded.studio_name == state.studio_name
    assert loaded.current_mission is not None
    assert loaded.current_mission.code == state.current_mission.code


def test_team_summary_shows_assigned_staff():
    state = new_game(777, "Team Studio")
    accept_mission(state)
    auto_cast(state)
    text = current_team_summary(state)
    assert "Team untuk mission" in text
    assert "Translator:" in text
    assert "Staff on mission:" in text


def test_bench_summary_excludes_current_assignees():
    state = new_game(888, "Bench Studio")
    mission = ensure_mission(state)
    auto_cast(state)
    text = bench_summary(state)
    assert f"Bench untuk mission {mission.code}" in text
    if mission.assigned_translator:
        assert mission.assigned_translator not in text
