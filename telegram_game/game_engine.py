from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional
import json
import random

PRIORITY_DEADLINES = {
    "superurgent": 1,
    "urgent": 2,
    "nonurgent": 3,
    "flexible": 4,
}

TITLE_PARTS_A = [
    "Neon", "Shadow", "Golden", "Silent", "Last", "Iron", "Moon", "Broken",
    "Crimson", "Wild", "Hidden", "Solar", "Ghost", "Blue", "Velvet", "Fallen",
]
TITLE_PARTS_B = [
    "Harbor", "Protocol", "Echo", "Circuit", "Frontier", "Mask", "Signal", "Crown",
    "River", "Storm", "Empire", "Voyage", "Run", "Garden", "Whisper", "Night",
]
LANGS = ["bn", "ms", "en"]
PRIORITIES = ["superurgent", "urgent", "nonurgent", "flexible"]
TRANSLATOR_NAMES = [
    "Alya", "Hafiz", "Rina", "Danish", "Mira", "Suri", "Zayn", "Nadia", "Farah", "Nurin", "Ilyas", "Rafi",
]
VO_NAMES_MALE = ["Ray", "Faiz", "Kamal", "Iman", "Riz", "Shahril", "Hakim", "Aqil", "Ammar", "Zul"]
VO_NAMES_FEMALE = ["Lina", "Ema", "Sara", "Yana", "Tina", "Ain", "Alya V", "Misha", "Qis", "Dina"]

UPGRADE_DEFAULTS = {
    "translator_lab": 0,
    "vo_booth": 0,
    "lounge": 0,
}

UPGRADE_ALIASES = {
    "translator": "translator_lab",
    "translator_lab": "translator_lab",
    "lab": "translator_lab",
    "trans": "translator_lab",
    "vo": "vo_booth",
    "booth": "vo_booth",
    "vo_booth": "vo_booth",
    "studio": "studio",
    "office": "studio",
    "expand": "studio",
    "expansion": "studio",
    "lounge": "lounge",
    "staff": "lounge",
    "rest": "lounge",
}


@dataclass
class Staff:
    name: str
    role_type: str  # translator / male / female
    skill: int
    speed: int
    energy: int = 100
    level: int = 1
    hire_cost: int = 0
    salary: int = 0
    source: str = "game"

    def power(self) -> float:
        return self.skill * 1.4 + self.speed * 1.1 + self.level * 4 + self.energy * 0.08


@dataclass
class RoleSlot:
    role: str
    lines: int
    gender: str


@dataclass
class Mission:
    code: str
    title: str
    year: int
    lang: str
    priority: str
    reward: int
    xp: int
    deadline_day: int
    translator_difficulty: int
    qa_threshold: int
    roles: List[RoleSlot]
    assigned_translator: Optional[str] = None
    assigned_roles: Dict[str, str] = field(default_factory=dict)
    accepted: bool = False
    source: str = "generated"


@dataclass
class GameState:
    user_id: int
    studio_name: str = "Studio Baru"
    day: int = 1
    coins: int = 120
    xp: int = 0
    wins: int = 0
    losses: int = 0
    studio_tier: int = 1
    current_mission: Optional[Mission] = None
    roster: List[Staff] = field(default_factory=list)
    market: List[Staff] = field(default_factory=list)
    upgrades: Dict[str, int] = field(default_factory=lambda: dict(UPGRADE_DEFAULTS))
    log: List[str] = field(default_factory=list)

    def level(self) -> int:
        return 1 + self.xp // 120


def _role_gender(role: str) -> str:
    return "male" if role.startswith("man") else "female"


def _hire_cost_for(role_type: str, skill: int, speed: int, level: int) -> int:
    base = 26 + skill + speed + level * 16
    if role_type == "translator":
        base += 18
    return int(round(base / 3.0))


def _salary_for(hire_cost: int, level: int) -> int:
    return max(4, hire_cost // 18 + max(0, level - 1))


def _starter_staff(name: str, role_type: str, skill: int, speed: int, level: int = 1) -> Staff:
    hire_cost = 0
    salary = max(4, (skill + speed) // 28 + level)
    return Staff(
        name=name,
        role_type=role_type,
        skill=skill,
        speed=speed,
        energy=100,
        level=level,
        hire_cost=hire_cost,
        salary=salary,
        source="starter",
    )


def _make_default_roster() -> List[Staff]:
    roster: List[Staff] = []
    rnd = random.Random(7)
    for name in TRANSLATOR_NAMES[:4]:
        roster.append(_starter_staff(name=name, role_type="translator", skill=rnd.randint(45, 70), speed=rnd.randint(45, 70)))
    for name in VO_NAMES_MALE[:4]:
        roster.append(_starter_staff(name=name, role_type="male", skill=rnd.randint(40, 72), speed=rnd.randint(40, 72)))
    for name in VO_NAMES_FEMALE[:4]:
        roster.append(_starter_staff(name=name, role_type="female", skill=rnd.randint(40, 72), speed=rnd.randint(40, 72)))
    return roster


def _all_name_pool(role_type: str) -> List[str]:
    if role_type == "translator":
        return TRANSLATOR_NAMES
    if role_type == "male":
        return VO_NAMES_MALE
    return VO_NAMES_FEMALE


def _unique_name(name: str, existing: set[str]) -> str:
    if name not in existing:
        return name
    idx = 2
    while f"{name} {idx}" in existing:
        idx += 1
    return f"{name} {idx}"


def total_salary(state: GameState) -> int:
    return sum(max(0, member.salary) for member in state.roster)


def _market_seed(state: GameState, seed: Optional[int] = None) -> int:
    if seed is not None:
        return seed
    return state.user_id * 1009 + state.day * 97 + state.studio_tier * 31 + state.level() * 13


def generate_market(state: GameState, seed: Optional[int] = None, count: Optional[int] = None) -> List[Staff]:
    rnd = random.Random(_market_seed(state, seed))
    count = count or min(8, 4 + state.studio_tier)
    existing = {member.name for member in state.roster}
    market: List[Staff] = []
    role_choices = ["translator", "male", "female", "male", "female"]
    for _ in range(count):
        role_type = rnd.choice(role_choices)
        base_skill = rnd.randint(44, 62) + state.studio_tier * 2 + min(8, state.level())
        base_speed = rnd.randint(42, 64) + state.studio_tier * 2 + min(6, state.day // 2)
        level = 1 + min(4, (state.studio_tier - 1) + rnd.randint(0, max(1, state.level() // 2)))
        if role_type == "translator":
            base_skill += 4
        hire_cost = _hire_cost_for(role_type, base_skill, base_speed, level)
        salary = _salary_for(hire_cost, level)
        raw_name = rnd.choice(_all_name_pool(role_type))
        name = _unique_name(raw_name, existing)
        existing.add(name)
        market.append(
            Staff(
                name=name,
                role_type=role_type,
                skill=min(95, base_skill),
                speed=min(92, base_speed),
                energy=100,
                level=level,
                hire_cost=hire_cost,
                salary=salary,
                source="market",
            )
        )
    market.sort(key=lambda member: (member.role_type, member.hire_cost, member.power()), reverse=True)
    return market


def refresh_market(state: GameState, seed: Optional[int] = None) -> List[Staff]:
    state.market = generate_market(state, seed=seed)
    state.log.append(f"Market refresh untuk hari {state.day}. {len(state.market)} recruit muncul.")
    return state.market


def new_game(user_id: int, studio_name: str = "Studio Baru") -> GameState:
    state = GameState(user_id=user_id, studio_name=studio_name, roster=_make_default_roster())
    refresh_market(state)
    state.log.append("Studio dibuka. Misi pertama menunggu.")
    return state


def _next_code(day: int, lang: str, counter: int = 1) -> str:
    return f"{lang.upper()}-{260300 + day:06d}-{counter:02d}"


def _mission_title(rnd: random.Random) -> str:
    return f"{rnd.choice(TITLE_PARTS_A)} {rnd.choice(TITLE_PARTS_B)}"


def generate_mission(state: GameState, seed: Optional[int] = None) -> Mission:
    rnd = random.Random(seed if seed is not None else (state.user_id * 1000 + state.day * 17 + state.level()))
    priority = rnd.choices(PRIORITIES, weights=[10, 30, 35, 25], k=1)[0]
    lang = rnd.choice(LANGS)
    year = rnd.randint(2018, 2026)
    role_count = rnd.randint(2, min(5, 2 + state.level() + max(0, state.studio_tier - 1)))
    roles: List[RoleSlot] = []
    for idx in range(1, role_count + 1):
        gender = rnd.choice(["male", "female"])
        prefix = "man" if gender == "male" else "fem"
        lines = rnd.randint(40, 180) + state.level() * rnd.randint(5, 20)
        roles.append(RoleSlot(role=f"{prefix}{idx}", lines=lines, gender=gender))

    reward = 60 + sum(r.lines for r in roles) // 5 + (10 * state.level()) + state.studio_tier * 6
    xp = 25 + len(roles) * 8 + (15 if priority == "superurgent" else 0)
    deadline_day = state.day + PRIORITY_DEADLINES[priority]
    translator_difficulty = 45 + len(roles) * 6 + (15 if priority in {"superurgent", "urgent"} else 0)
    qa_threshold = 55 + len(roles) * 5 + (10 if priority == "superurgent" else 0)
    return Mission(
        code=_next_code(state.day, lang),
        title=_mission_title(rnd),
        year=year,
        lang=lang,
        priority=priority,
        reward=reward,
        xp=xp,
        deadline_day=deadline_day,
        translator_difficulty=translator_difficulty,
        qa_threshold=qa_threshold,
        roles=roles,
    )


def ensure_mission(state: GameState) -> Mission:
    if state.current_mission is None:
        state.current_mission = generate_mission(state)
    return state.current_mission


def find_staff(state: GameState, name: str) -> Optional[Staff]:
    target = name.strip().lower()
    for member in state.roster:
        if member.name.lower() == target:
            return member
    return None


def find_market_staff(state: GameState, name: str) -> Optional[Staff]:
    target = name.strip().lower()
    for member in state.market:
        if member.name.lower() == target:
            return member
    return None


def auto_cast(state: GameState) -> Dict[str, str]:
    mission = ensure_mission(state)
    picks: Dict[str, str] = {}
    translator_pool = [s for s in state.roster if s.role_type == "translator" and s.energy > 0]
    if translator_pool:
        best_translator = max(translator_pool, key=lambda s: s.power())
        mission.assigned_translator = best_translator.name
        picks["translator"] = best_translator.name

    used = set()
    for role in mission.roles:
        pool = [s for s in state.roster if s.role_type == role.gender and s.energy > 0 and s.name not in used]
        if not pool:
            continue
        best = max(pool, key=lambda s: s.power())
        mission.assigned_roles[role.role] = best.name
        used.add(best.name)
        picks[role.role] = best.name
    return picks


def assign_translator(state: GameState, translator_name: str) -> str:
    mission = ensure_mission(state)
    member = find_staff(state, translator_name)
    if not member or member.role_type != "translator":
        raise ValueError("Translator tak wujud.")
    mission.assigned_translator = member.name
    return member.name


def assign_role(state: GameState, role_name: str, staff_name: str) -> str:
    mission = ensure_mission(state)
    role = next((r for r in mission.roles if r.role.lower() == role_name.lower()), None)
    if not role:
        raise ValueError("Role tak wujud.")
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    if member.role_type != role.gender:
        raise ValueError("Gender role tak match.")
    mission.assigned_roles[role.role] = member.name
    return member.name


def clear_assignments(state: GameState) -> Mission:
    mission = ensure_mission(state)
    mission.assigned_translator = None
    mission.assigned_roles.clear()
    return mission


def accept_mission(state: GameState) -> Mission:
    mission = ensure_mission(state)
    mission.accepted = True
    state.log.append(f"Terima misi {mission.code}.")
    return mission


def _translator_score(state: GameState, mission: Mission) -> float:
    if not mission.assigned_translator:
        return 0.0
    tr = find_staff(state, mission.assigned_translator)
    if not tr:
        return 0.0
    bonus = state.upgrades.get("translator_lab", 0) * 2.5
    return tr.skill * 0.9 + tr.speed * 0.6 + tr.level * 5 + bonus - max(0, mission.translator_difficulty - tr.skill) * 0.4


def _vo_score(state: GameState, mission: Mission) -> float:
    if not mission.roles:
        return 0.0
    total = 0.0
    booth_bonus = state.upgrades.get("vo_booth", 0) * 2.0
    for role in mission.roles:
        name = mission.assigned_roles.get(role.role)
        if not name:
            return 0.0
        vo = find_staff(state, name)
        if not vo:
            return 0.0
        role_weight = 1 + role.lines / 120.0
        total += (vo.skill * 0.85 + vo.speed * 0.5 + vo.level * 4 + booth_bonus) / role_weight
    return total / len(mission.roles)


def _deadline_penalty(state: GameState, mission: Mission) -> float:
    late_days = max(0, state.day - mission.deadline_day)
    return late_days * 12.0


def _consume_energy(state: GameState, mission: Mission) -> None:
    names = set(mission.assigned_roles.values())
    if mission.assigned_translator:
        names.add(mission.assigned_translator)
    for member in state.roster:
        if member.name in names:
            member.energy = max(10, member.energy - 18)
        else:
            member.energy = min(100, member.energy + 8)


def resolve_submission(state: GameState) -> Dict[str, object]:
    mission = ensure_mission(state)
    if not mission.accepted:
        raise ValueError("Terima misi dulu.")
    if not mission.assigned_translator:
        raise ValueError("Belum assign translator.")
    missing = [r.role for r in mission.roles if r.role not in mission.assigned_roles]
    if missing:
        raise ValueError(f"Masih ada role belum assign: {', '.join(missing)}")

    translator_score = _translator_score(state, mission)
    vo_score = _vo_score(state, mission)
    qa_score = translator_score * 0.45 + vo_score * 0.85 - _deadline_penalty(state, mission)
    passed = qa_score >= mission.qa_threshold

    reward = mission.reward if passed else max(15, mission.reward // 5)
    gained_xp = mission.xp if passed else max(8, mission.xp // 4)
    state.coins += reward
    state.xp += gained_xp
    state.wins += 1 if passed else 0
    state.losses += 0 if passed else 1
    _consume_energy(state, mission)

    if passed:
        state.log.append(f"Misi {mission.code} lulus QA. +{reward} coins, +{gained_xp} XP")
    else:
        state.log.append(f"Misi {mission.code} gagal QA. +{reward} coins saguhati, +{gained_xp} XP")

    result = {
        "passed": passed,
        "qa_score": round(qa_score, 1),
        "threshold": mission.qa_threshold,
        "reward": reward,
        "xp": gained_xp,
        "code": mission.code,
        "title": mission.title,
    }
    state.current_mission = None
    return result


def hire_staff(state: GameState, staff_name: str) -> Staff:
    candidate = find_market_staff(state, staff_name)
    if not candidate:
        raise ValueError("Staff market tak jumpa.")
    if state.coins < candidate.hire_cost:
        raise ValueError(f"Coins tak cukup. Perlu {candidate.hire_cost}.")
    state.coins -= candidate.hire_cost
    state.roster.append(candidate)
    state.market = [member for member in state.market if member.name.lower() != candidate.name.lower()]
    state.log.append(f"Hire {candidate.name} [{candidate.role_type}] dengan kos {candidate.hire_cost}.")
    return candidate


def fire_staff(state: GameState, staff_name: str) -> Staff:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    mission = state.current_mission
    if mission and (member.name == mission.assigned_translator or member.name in mission.assigned_roles.values()):
        raise ValueError("Tak boleh fire staff yang sedang assigned pada mission.")
    kind_count = sum(1 for item in state.roster if item.role_type == member.role_type)
    if kind_count <= 1:
        raise ValueError("Tak boleh fire staff terakhir untuk kategori ini.")
    state.roster = [item for item in state.roster if item.name.lower() != member.name.lower()]
    state.log.append(f"Staff {member.name} diberhentikan.")
    return member


def _upgrade_cost(state: GameState, target: str) -> int:
    normalized = UPGRADE_ALIASES.get(target.strip().lower(), target.strip().lower())
    if normalized == "studio":
        return 150 + (state.studio_tier - 1) * 120
    if normalized == "translator_lab":
        return 90 + state.upgrades.get("translator_lab", 0) * 60
    if normalized == "vo_booth":
        return 100 + state.upgrades.get("vo_booth", 0) * 70
    if normalized == "lounge":
        return 80 + state.upgrades.get("lounge", 0) * 50
    raise ValueError("Upgrade tak dikenali.")


def upgrade_studio(state: GameState, target: str) -> Dict[str, object]:
    normalized = UPGRADE_ALIASES.get(target.strip().lower(), target.strip().lower())
    cost = _upgrade_cost(state, normalized)
    if state.coins < cost:
        raise ValueError(f"Coins tak cukup. Perlu {cost}.")
    state.coins -= cost

    if normalized == "studio":
        state.studio_tier += 1
        refresh_market(state)
        state.log.append(f"Studio expand ke tier {state.studio_tier}. Kos {cost}.")
        return {"target": "studio", "cost": cost, "level": state.studio_tier}

    if normalized == "translator_lab":
        state.upgrades["translator_lab"] = state.upgrades.get("translator_lab", 0) + 1
        for member in state.roster:
            if member.role_type == "translator":
                member.skill = min(99, member.skill + 3)
                member.speed = min(95, member.speed + 1)
        state.log.append(f"Translator Lab naik ke lvl {state.upgrades['translator_lab']}. Kos {cost}.")
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized]}

    if normalized == "vo_booth":
        state.upgrades["vo_booth"] = state.upgrades.get("vo_booth", 0) + 1
        for member in state.roster:
            if member.role_type in {"male", "female"}:
                member.skill = min(99, member.skill + 3)
                member.speed = min(95, member.speed + 1)
        state.log.append(f"VO Booth naik ke lvl {state.upgrades['vo_booth']}. Kos {cost}.")
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized]}

    if normalized == "lounge":
        state.upgrades["lounge"] = state.upgrades.get("lounge", 0) + 1
        for member in state.roster:
            member.energy = min(100, member.energy + 12)
        state.log.append(f"Lounge naik ke lvl {state.upgrades['lounge']}. Kos {cost}.")
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized]}

    raise ValueError("Upgrade tak dikenali.")


def next_day(state: GameState) -> None:
    state.day += 1
    payroll = total_salary(state)
    had_payroll_shortage = state.coins < payroll
    state.coins = max(0, state.coins - payroll)
    recovery = 12 + state.upgrades.get("lounge", 0) * 4
    for member in state.roster:
        member.energy = min(100, member.energy + recovery)
        if had_payroll_shortage:
            member.energy = max(10, member.energy - 8)
    refresh_market(state)
    if had_payroll_shortage:
        state.log.append(f"Hari {state.day} bermula. Payroll {payroll} tak cukup, morale jatuh.")
    else:
        state.log.append(f"Hari {state.day} bermula. Payroll dibayar: {payroll}.")


def _assigned_staff_names(mission: Mission) -> set[str]:
    names = set(mission.assigned_roles.values())
    if mission.assigned_translator:
        names.add(mission.assigned_translator)
    return names


def roster_summary(state: GameState) -> str:
    lines = [
        f"🏢 {state.studio_name} — Day {state.day} — Coins {state.coins} — XP {state.xp} — Level {state.level()}",
        f"Studio tier {state.studio_tier} | Payroll {total_salary(state)} | Upgrades T{state.upgrades.get('translator_lab', 0)} / V{state.upgrades.get('vo_booth', 0)} / L{state.upgrades.get('lounge', 0)}",
    ]
    for kind, label in [("translator", "Translator"), ("male", "VO Male"), ("female", "VO Female")]:
        lines.append(f"\n{label}:")
        for member in [s for s in state.roster if s.role_type == kind]:
            lines.append(
                f"- {member.name}: skill {member.skill}, speed {member.speed}, energy {member.energy}, lvl {member.level}, salary {member.salary}"
            )
    return "\n".join(lines)


def current_team_summary(state: GameState) -> str:
    mission = ensure_mission(state)
    lines = [f"🎯 Team untuk mission {mission.code} — {mission.title}"]
    lines.append(f"Translator: {mission.assigned_translator or '-'}")
    for role in mission.roles:
        lines.append(f"- {role.role}: {mission.assigned_roles.get(role.role, '-')}")
    assigned_names = _assigned_staff_names(mission)
    if assigned_names:
        lines.append("")
        lines.append("Staff on mission:")
        for member in sorted((s for s in state.roster if s.name in assigned_names), key=lambda s: (s.role_type, s.name.lower())):
            lines.append(
                f"- {member.name} [{member.role_type}] power {round(member.power(), 1)} | energy {member.energy} | lvl {member.level}"
            )
    else:
        lines.append("")
        lines.append("Belum ada staff assign lagi.")
    return "\n".join(lines)


def bench_summary(state: GameState) -> str:
    mission = ensure_mission(state)
    assigned_names = _assigned_staff_names(mission)
    lines = [f"🪑 Bench untuk mission {mission.code}"]
    for kind, label in [("translator", "Translator bench"), ("male", "VO Male bench"), ("female", "VO Female bench")]:
        lines.append(f"\n{label}:")
        bench = [s for s in state.roster if s.role_type == kind and s.name not in assigned_names]
        if not bench:
            lines.append("- kosong")
            continue
        bench = sorted(bench, key=lambda s: (s.energy, s.power(), s.level), reverse=True)
        for member in bench:
            lines.append(
                f"- {member.name}: power {round(member.power(), 1)}, skill {member.skill}, speed {member.speed}, energy {member.energy}, lvl {member.level}"
            )
    return "\n".join(lines)


def market_summary(state: GameState) -> str:
    if not state.market:
        return "🛒 Market kosong. Guna /nextday untuk refresh."
    lines = [f"🛒 Recruitment market — Day {state.day} — Studio tier {state.studio_tier}"]
    for kind, label in [("translator", "Translator recruits"), ("male", "VO Male recruits"), ("female", "VO Female recruits")]:
        lines.append(f"\n{label}:")
        items = [s for s in state.market if s.role_type == kind]
        if not items:
            lines.append("- kosong")
            continue
        items = sorted(items, key=lambda s: (s.hire_cost, s.power()), reverse=True)
        for member in items:
            lines.append(
                f"- {member.name}: power {round(member.power(), 1)}, skill {member.skill}, speed {member.speed}, lvl {member.level}, hire {member.hire_cost}, salary {member.salary}"
            )
    return "\n".join(lines)


def studio_summary(state: GameState) -> str:
    translator_cost = _upgrade_cost(state, "translator_lab")
    vo_cost = _upgrade_cost(state, "vo_booth")
    lounge_cost = _upgrade_cost(state, "lounge")
    studio_cost = _upgrade_cost(state, "studio")
    return (
        f"🏢 {state.studio_name}\n"
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()}\n"
        f"Wins {state.wins} | Losses {state.losses}\n"
        f"Studio tier: {state.studio_tier}\n"
        f"Payroll per day: {total_salary(state)}\n"
        f"Roster size: {len(state.roster)} | Market size: {len(state.market)}\n\n"
        f"Upgrades:\n"
        f"- translator_lab lvl {state.upgrades.get('translator_lab', 0)} (next {translator_cost})\n"
        f"- vo_booth lvl {state.upgrades.get('vo_booth', 0)} (next {vo_cost})\n"
        f"- lounge lvl {state.upgrades.get('lounge', 0)} (next {lounge_cost})\n"
        f"- studio tier {state.studio_tier} (next expansion {studio_cost})"
    )


def mission_summary(mission: Mission) -> str:
    roles = "\n".join(f"- {r.role} ({r.gender}) — {r.lines} lines" for r in mission.roles)
    assigned = []
    assigned.append(f"Translator: {mission.assigned_translator or '-'}")
    for r in mission.roles:
        assigned.append(f"{r.role}: {mission.assigned_roles.get(r.role, '-')}")
    return (
        f"🎬 {mission.title} ({mission.year})\n"
        f"Code: {mission.code}\n"
        f"Lang: {mission.lang.upper()}\n"
        f"Priority: {mission.priority}\n"
        f"Deadline day: {mission.deadline_day}\n"
        f"Reward: {mission.reward} coins | XP: {mission.xp}\n"
        f"Source: {mission.source}\n\n"
        f"Roles:\n{roles}\n\n"
        f"Assignment:\n" + "\n".join(assigned)
    )


def latest_log(state: GameState, limit: int = 8) -> str:
    items = state.log[-limit:]
    return "\n".join(f"- {item}" for item in items) if items else "- Tiada log"


def save_state(state: GameState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_state(path: Path) -> Optional[GameState]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    mission = data.get("current_mission")
    if mission:
        mission_obj = Mission(
            **{k: v for k, v in mission.items() if k != "roles"},
            roles=[RoleSlot(**r) for r in mission.get("roles", [])],
        )
    else:
        mission_obj = None
    roster = [Staff(**s) for s in data.get("roster", [])]
    market = [Staff(**s) for s in data.get("market", [])]
    upgrades = dict(UPGRADE_DEFAULTS)
    upgrades.update(data.get("upgrades", {}) or {})
    state = GameState(
        user_id=data["user_id"],
        studio_name=data.get("studio_name", "Studio Baru"),
        day=data.get("day", 1),
        coins=data.get("coins", 120),
        xp=data.get("xp", 0),
        wins=data.get("wins", 0),
        losses=data.get("losses", 0),
        studio_tier=data.get("studio_tier", 1),
        current_mission=mission_obj,
        roster=roster,
        market=market,
        upgrades=upgrades,
        log=data.get("log", []),
    )
    if not state.market:
        refresh_market(state)
    return state
