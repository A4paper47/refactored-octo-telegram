from pathlib import Path
import tempfile

import pytest

from telegram_game.game_engine import (
    accept_mission,
    auto_cast,
    bench_summary,
    client_summary,
    current_team_summary,
    ensure_mission,
    fire_staff,
    hire_staff,
    load_state,
    market_summary,
    mission_summary,
    new_game,
    next_day,
    reputation_summary,
    resolve_submission,
    save_state,
    studio_summary,
    upgrade_studio,
)


def test_generate_and_autocast():
    state = new_game(123, "Test Studio")
    mission = ensure_mission(state)
    assert mission.code
    assert mission.roles
    assert mission.client_name
    picks = auto_cast(state)
    assert "translator" in picks
    assert len(mission.assigned_roles) == len(mission.roles)


def test_submit_flow_passes_after_accept_and_autocast():
    state = new_game(123, "Test Studio")
    rep_before = state.reputation
    accept_mission(state)
    auto_cast(state)
    result = resolve_submission(state)
    assert "passed" in result
    assert result["reward"] >= 15
    assert "rep_change" in result
    assert state.reputation >= 0
    if result["passed"]:
        assert state.reputation >= rep_before
    assert state.current_mission is None


def test_next_day_recovers_energy_and_burnout():
    state = new_game(123, "Test Studio")
    accept_mission(state)
    auto_cast(state)
    resolve_submission(state)
    min_energy_before = min(s.energy for s in state.roster)
    max_burnout_before = max(s.burnout for s in state.roster)
    next_day(state)
    min_energy_after = min(s.energy for s in state.roster)
    max_burnout_after = max(s.burnout for s in state.roster)
    assert min_energy_after >= min_energy_before
    assert max_burnout_after <= max_burnout_before


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
    assert loaded.market
    assert loaded.studio_tier == state.studio_tier
    assert loaded.reputation == state.reputation


def test_team_summary_shows_assigned_staff_and_client():
    state = new_game(777, "Team Studio")
    accept_mission(state)
    auto_cast(state)
    text = current_team_summary(state)
    assert "Team untuk mission" in text
    assert "Client:" in text
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


def test_hire_from_market_spends_coins_and_moves_staff():
    state = new_game(900, "Hire Studio")
    state.coins = 999
    candidate = state.market[0]
    coins_before = state.coins
    roster_before = len(state.roster)
    hired = hire_staff(state, candidate.name)
    assert hired.name == candidate.name
    assert len(state.roster) == roster_before + 1
    assert len(state.market) >= 1
    assert state.coins == coins_before - candidate.hire_cost
    assert any(member.name == candidate.name for member in state.roster)


def test_fire_assigned_staff_is_blocked():
    state = new_game(901, "Fire Studio")
    accept_mission(state)
    auto_cast(state)
    mission = state.current_mission
    assert mission is not None and mission.assigned_translator is not None
    with pytest.raises(ValueError):
        fire_staff(state, mission.assigned_translator)


def test_upgrade_translator_boosts_stats_and_spends_coins():
    state = new_game(902, "Upgrade Studio")
    state.coins = 500
    translator = next(member for member in state.roster if member.role_type == "translator")
    skill_before = translator.skill
    info = upgrade_studio(state, "translator")
    assert info["target"] == "translator_lab"
    assert info["level"] == 1
    assert translator.skill >= skill_before + 3
    assert state.coins < 500


def test_next_day_refreshes_market_and_updates_payroll_log():
    state = new_game(903, "Economy Studio")
    state.coins = 999
    market_before = [member.name for member in state.market]
    next_day(state)
    market_after = [member.name for member in state.market]
    assert state.day == 2
    assert market_before != market_after
    assert any("Payroll" in entry for entry in state.log[-3:])


def test_market_and_studio_summary_show_new_layers():
    state = new_game(904, "Summary Studio")
    market = market_summary(state)
    assert "Recruitment market" in market
    assert "rarity" in market
    summary = studio_summary(state)
    assert "Payroll per day" in summary
    assert "Reputation" in summary
    assert "Unlocked client tiers" in summary


def test_client_and_reputation_summary_show_progression():
    state = new_game(905, "Client Studio")
    ensure_mission(state)
    assert "Client desk" in client_summary(state)
    rep_text = reputation_summary(state)
    assert "Reputation board" in rep_text
    assert "Unlocked clients" in rep_text


def test_mission_summary_shows_client_and_rep_reward():
    state = new_game(906, "Mission Studio")
    mission = ensure_mission(state)
    text = mission_summary(mission)
    assert "Client:" in text
    assert "Rep:" in text
