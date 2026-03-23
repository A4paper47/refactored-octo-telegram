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

RARITY_MULTIPLIER = {
    "common": 1.00,
    "rare": 1.08,
    "epic": 1.18,
    "legend": 1.30,
}

RARITY_ICON = {
    "common": "⚪",
    "rare": "🔵",
    "epic": "🟣",
    "legend": "🟡",
}

TRAIT_POOL = {
    "translator": ["polyglot", "sprinter", "perfectionist", "veteran", "workhorse", "resilient"],
    "male": ["charmer", "natural", "sprinter", "veteran", "workhorse", "resilient"],
    "female": ["charmer", "natural", "sprinter", "veteran", "workhorse", "resilient"],
}

CLIENT_POOL = [
    {"name": "Indie Spark", "tier": "indie", "reward_mult": 0.95, "xp_bonus": 0, "rep": 1},
    {"name": "Silver Lantern", "tier": "broadcast", "reward_mult": 1.00, "xp_bonus": 4, "rep": 1},
    {"name": "Nova Stream", "tier": "premium", "reward_mult": 1.12, "xp_bonus": 8, "rep": 2},
    {"name": "Titan Global", "tier": "enterprise", "reward_mult": 1.25, "xp_bonus": 12, "rep": 3},
]

CLIENT_UNLOCK_REP = {
    "indie": 0,
    "broadcast": 6,
    "premium": 16,
    "enterprise": 30,
}

TRAINING_FOCUS = {
    "balanced": {"skill": 1, "speed": 1, "energy": 10, "burnout": 4},
    "skill": {"skill": 2, "speed": 0, "energy": 12, "burnout": 5},
    "speed": {"skill": 0, "speed": 2, "energy": 12, "burnout": 5},
}

MISSION_MODIFIERS = {
    "rush_rewrite": {
        "label": "Rush Rewrite",
        "desc": "Script berubah saat akhir, translator kena laju.",
        "reward": 18,
        "xp": 6,
        "translator_difficulty": 10,
        "qa_threshold": 5,
    },
    "lip_sync_heavy": {
        "label": "Lip-Sync Heavy",
        "desc": "Timing ketat, VO perlu lebih tepat.",
        "reward": 16,
        "xp": 7,
        "translator_difficulty": 4,
        "qa_threshold": 6,
    },
    "ensemble_scene": {
        "label": "Ensemble Scene",
        "desc": "Banyak overlap dialog, cast kena seimbang.",
        "reward": 14,
        "xp": 5,
        "translator_difficulty": 3,
        "qa_threshold": 4,
    },
    "clean_audio": {
        "label": "Clean Audio",
        "desc": "Rakaman asal bersih, QA jadi sedikit mudah.",
        "reward": 8,
        "xp": 3,
        "translator_difficulty": -2,
        "qa_threshold": -3,
    },
    "premium_notes": {
        "label": "Premium Notes",
        "desc": "Client bagi arahan detail, reward naik tapi QA lebih cerewet.",
        "reward": 20,
        "xp": 8,
        "translator_difficulty": 5,
        "qa_threshold": 5,
    },
    "glossary_lock": {
        "label": "Glossary Lock",
        "desc": "Term kena ikut glossary ketat.",
        "reward": 12,
        "xp": 4,
        "translator_difficulty": 7,
        "qa_threshold": 2,
    },
}

EQUIPMENT_CATALOG = {
    "focus_notes": {
        "label": "Focus Notes",
        "desc": "Nota fokus untuk translator.",
        "roles": ["translator"],
        "cost": 42,
        "skill": 5,
        "speed": 1,
        "translator_bonus": 5,
    },
    "glossary_pad": {
        "label": "Glossary Pad",
        "desc": "Pad istilah untuk bahasa campur.",
        "roles": ["translator"],
        "cost": 54,
        "skill": 4,
        "translator_bonus": 8,
    },
    "wave_mic": {
        "label": "Wave Mic",
        "desc": "Mic studio asas untuk VO.",
        "roles": ["male", "female"],
        "cost": 48,
        "skill": 4,
        "speed": 2,
        "vo_bonus": 6,
    },
    "pop_filter": {
        "label": "Pop Filter",
        "desc": "Bantu take lebih bersih masa lip-sync susah.",
        "roles": ["male", "female"],
        "cost": 52,
        "skill": 3,
        "speed": 1,
        "vo_bonus": 5,
        "modifier_bonus": {"lip_sync_heavy": 6},
    },
    "rush_kit": {
        "label": "Rush Kit",
        "desc": "Kit kerja urgent untuk semua role.",
        "roles": ["translator", "male", "female"],
        "cost": 66,
        "speed": 5,
        "urgent_bonus": 8,
    },
    "calm_tea": {
        "label": "Calm Tea",
        "desc": "Kurangkan burnout sebelum sesi penting.",
        "roles": ["translator", "male", "female"],
        "cost": 38,
        "burnout_relief": 10,
        "premium_bonus": 3,
    },
}

ACHIEVEMENT_ORDER = [
    "first_win",
    "team_builder",
    "trainer",
    "burnout_clinic",
    "rep_star",
    "studio_tier_2",
]

ACHIEVEMENT_META = {
    "first_win": {"title": "First Dub Cleared", "desc": "Luluskan 1 mission", "coins": 40, "xp": 20},
    "team_builder": {"title": "Team Builder", "desc": "Miliki 15 staff atau lebih", "coins": 60, "xp": 24},
    "trainer": {"title": "Training Arc", "desc": "Buat 3 sesi training", "coins": 45, "xp": 24},
    "burnout_clinic": {"title": "Burnout Clinic", "desc": "Buat 4 sesi rest", "coins": 35, "xp": 18},
    "rep_star": {"title": "Reputation Rising", "desc": "Capai reputation 20", "coins": 80, "xp": 32},
    "studio_tier_2": {"title": "Second Floor", "desc": "Naikkan studio ke tier 2", "coins": 90, "xp": 40},
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
    rarity: str = "common"
    traits: List[str] = field(default_factory=list)
    burnout: int = 0
    training_sessions: int = 0
    equipped: Optional[str] = None

    def power(self) -> float:
        rarity_bonus = RARITY_MULTIPLIER.get(self.rarity, 1.0)
        burnout_penalty = min(24.0, self.burnout * 0.32)
        return (self.skill * 1.4 + self.speed * 1.1 + self.level * 4 + self.energy * 0.08) * rarity_bonus - burnout_penalty


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
    client_name: str = "Indie Spark"
    client_tier: str = "indie"
    reputation_reward: int = 1
    modifiers: List[str] = field(default_factory=list)


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
    reputation: int = 10
    clients_seen: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    total_trainings: int = 0
    total_rests: int = 0
    inventory: Dict[str, int] = field(default_factory=dict)
    total_chests: int = 0

    def level(self) -> int:
        return 1 + self.xp // 120


def _role_gender(role: str) -> str:
    return "male" if role.startswith("man") else "female"


def _hire_cost_for(role_type: str, skill: int, speed: int, level: int, rarity: str = "common") -> int:
    base = 26 + skill + speed + level * 16
    if role_type == "translator":
        base += 18
    base *= RARITY_MULTIPLIER.get(rarity, 1.0)
    return int(round(base / 3.0))


def _salary_for(hire_cost: int, level: int, rarity: str = "common") -> int:
    base = max(4, hire_cost // 18 + max(0, level - 1))
    return int(round(base * (1 + max(0.0, RARITY_MULTIPLIER.get(rarity, 1.0) - 1.0) * 0.6)))


def _pick_traits(role_type: str, rnd: random.Random, rarity: str, forced_count: Optional[int] = None) -> List[str]:
    pool = list(TRAIT_POOL.get(role_type, []))
    if not pool:
        return []
    default_count = {
        "common": rnd.choice([0, 1]),
        "rare": 1,
        "epic": 2,
        "legend": 2,
    }[rarity]
    count = forced_count if forced_count is not None else default_count
    count = max(0, min(count, len(pool)))
    rnd.shuffle(pool)
    return sorted(pool[:count])


def _starter_staff(name: str, role_type: str, skill: int, speed: int, level: int = 1, seed: int = 0) -> Staff:
    rnd = random.Random(seed + len(name) * 17 + level * 5)
    rarity = "rare" if rnd.random() < 0.18 else "common"
    skill = min(95, int(round(skill * (1 + (RARITY_MULTIPLIER[rarity] - 1) * 0.35))) + (1 if rarity != "common" else 0))
    speed = min(92, int(round(speed * (1 + (RARITY_MULTIPLIER[rarity] - 1) * 0.30))) + (1 if rarity == "rare" else 0))
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
        rarity=rarity,
        traits=_pick_traits(role_type, rnd, rarity),
        burnout=0,
    )


def _make_default_roster() -> List[Staff]:
    roster: List[Staff] = []
    rnd = random.Random(7)
    for name in TRANSLATOR_NAMES[:4]:
        roster.append(_starter_staff(name=name, role_type="translator", skill=rnd.randint(45, 70), speed=rnd.randint(45, 70), seed=101))
    for name in VO_NAMES_MALE[:4]:
        roster.append(_starter_staff(name=name, role_type="male", skill=rnd.randint(40, 72), speed=rnd.randint(40, 72), seed=202))
    for name in VO_NAMES_FEMALE[:4]:
        roster.append(_starter_staff(name=name, role_type="female", skill=rnd.randint(40, 72), speed=rnd.randint(40, 72), seed=303))
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


def _pick_rarity(rnd: random.Random, studio_tier: int, level: int) -> str:
    weights = {
        "common": 68,
        "rare": 24,
        "epic": 7,
        "legend": 1,
    }
    weights["common"] = max(32, weights["common"] - studio_tier * 4 - min(14, level * 2))
    weights["rare"] = min(40, weights["rare"] + studio_tier * 3 + min(10, level))
    weights["epic"] = min(20, weights["epic"] + max(0, studio_tier - 1) * 2 + max(0, level - 2))
    weights["legend"] = min(8, weights["legend"] + max(0, studio_tier - 2) + max(0, level - 4) // 2)
    return rnd.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]


def total_salary(state: GameState) -> int:
    return sum(max(0, member.salary) for member in state.roster)


def _market_seed(state: GameState, seed: Optional[int] = None) -> int:
    if seed is not None:
        return seed
    return state.user_id * 1009 + state.day * 97 + state.studio_tier * 31 + state.level() * 13 + state.reputation * 19


def _format_traits(member: Staff) -> str:
    return ", ".join(member.traits) if member.traits else "-"


def _equipment_meta(item_key: Optional[str]) -> dict:
    return EQUIPMENT_CATALOG.get((item_key or "").strip().lower(), {})


def _equipment_label(item_key: Optional[str]) -> str:
    meta = _equipment_meta(item_key)
    return meta.get("label") or (item_key or "-")


def _modifier_label(modifier: str) -> str:
    return MISSION_MODIFIERS.get(modifier, {}).get("label", modifier)


def _modifier_desc(modifier: str) -> str:
    return MISSION_MODIFIERS.get(modifier, {}).get("desc", modifier)


def _equipment_bonus(member: Staff, mission: Mission, role: Optional[RoleSlot] = None, translator: bool = False) -> float:
    meta = _equipment_meta(member.equipped)
    if not meta:
        return 0.0
    score = float(meta.get("skill", 0)) * 0.7 + float(meta.get("speed", 0)) * 0.55
    if translator:
        score += float(meta.get("translator_bonus", 0))
    else:
        score += float(meta.get("vo_bonus", 0))
    if mission.priority in {"urgent", "superurgent"}:
        score += float(meta.get("urgent_bonus", 0))
    if mission.client_tier in {"premium", "enterprise"}:
        score += float(meta.get("premium_bonus", 0))
    if role is not None and role.lines >= 90:
        score += float(meta.get("long_take_bonus", 0))
    modifier_bonus = meta.get("modifier_bonus") or {}
    for modifier in mission.modifiers:
        score += float(modifier_bonus.get(modifier, 0))
    if meta.get("burnout_relief"):
        score += min(float(meta.get("burnout_relief", 0)) * 0.45, max(0.0, member.burnout * 0.18))
    return score


def _modifier_penalty(member: Staff, mission: Mission, translator: bool = False) -> float:
    penalty = 0.0
    modifier_set = set(mission.modifiers)
    if translator and "rush_rewrite" in modifier_set:
        penalty += max(0.0, 7.0 - member.speed * 0.04)
    if translator and "glossary_lock" in modifier_set and "polyglot" not in member.traits:
        penalty += 4.5
    if not translator and "lip_sync_heavy" in modifier_set and "natural" not in member.traits:
        penalty += 4.0
    if "premium_notes" in modifier_set and "perfectionist" not in member.traits:
        penalty += 2.5
    return penalty


def _trait_bonus(member: Staff, mission: Mission, role: Optional[RoleSlot] = None, translator: bool = False) -> float:
    traits = set(member.traits)
    bonus = 0.0
    if "polyglot" in traits and translator:
        bonus += 8.0 if mission.lang in {"bn", "ms"} else 5.0
    if "sprinter" in traits and mission.priority in {"urgent", "superurgent"}:
        bonus += 7.0
    if "perfectionist" in traits:
        bonus += 5.5
    if "natural" in traits and role is not None:
        bonus += min(8.0, role.lines / 48.0)
    if "charmer" in traits and not translator:
        bonus += 5.0
    if "veteran" in traits:
        bonus += 6.0
    if "workhorse" in traits:
        bonus += 4.0
    if "resilient" in traits:
        bonus += max(0.0, 6.0 - member.burnout * 0.08)
    if mission.client_tier in {"premium", "enterprise"} and "perfectionist" in traits:
        bonus += 3.0
    if "rush_rewrite" in mission.modifiers and translator and "polyglot" in traits:
        bonus += 4.0
    if "ensemble_scene" in mission.modifiers and not translator and "charmer" in traits:
        bonus += 3.5
    return bonus


def _burnout_penalty(member: Staff) -> float:
    penalty = member.burnout * 0.36
    if "resilient" in member.traits:
        penalty *= 0.65
    return min(28.0, penalty)


def _client_for_state(state: GameState, rnd: random.Random) -> dict:
    unlocked = [client for client in CLIENT_POOL if state.reputation >= CLIENT_UNLOCK_REP.get(client["tier"], 0)]
    if not unlocked:
        unlocked = [CLIENT_POOL[0]]
    weights = []
    for client in unlocked:
        base = {
            "indie": 40,
            "broadcast": 30,
            "premium": 18,
            "enterprise": 8,
        }[client["tier"]]
        base += state.studio_tier * 2
        if client["tier"] in {"premium", "enterprise"}:
            base += max(0, state.reputation - CLIENT_UNLOCK_REP[client["tier"]])
        weights.append(base)
    return rnd.choices(unlocked, weights=weights, k=1)[0]


def _remember_client(state: GameState, client_name: str) -> None:
    if client_name and client_name not in state.clients_seen:
        state.clients_seen.append(client_name)
        state.clients_seen.sort()


def _pick_mission_modifiers(state: GameState, rnd: random.Random, priority: str, client_tier: str) -> List[str]:
    pool = list(MISSION_MODIFIERS.keys())
    weights = []
    for key in pool:
        weight = 10
        if key == "rush_rewrite" and priority in {"urgent", "superurgent"}:
            weight += 12
        if key == "premium_notes" and client_tier in {"premium", "enterprise"}:
            weight += 10
        if key == "clean_audio":
            weight += 6
        if key == "ensemble_scene":
            weight += 4
        weights.append(weight)
    count = 1 if rnd.random() < 0.72 else 2
    picked: List[str] = []
    for _ in range(count):
        choice = rnd.choices(pool, weights=weights, k=1)[0]
        if choice not in picked:
            picked.append(choice)
    return picked


def _apply_modifier_package(mission: Mission) -> Mission:
    for modifier in mission.modifiers:
        meta = MISSION_MODIFIERS.get(modifier, {})
        mission.reward += int(meta.get("reward", 0))
        mission.xp += int(meta.get("xp", 0))
        mission.translator_difficulty += int(meta.get("translator_difficulty", 0))
        mission.qa_threshold += int(meta.get("qa_threshold", 0))
    mission.reward = max(20, mission.reward)
    mission.xp = max(10, mission.xp)
    mission.translator_difficulty = max(10, mission.translator_difficulty)
    mission.qa_threshold = max(20, mission.qa_threshold)
    return mission


def generate_market(state: GameState, seed: Optional[int] = None, count: Optional[int] = None) -> List[Staff]:
    rnd = random.Random(_market_seed(state, seed))
    count = count or min(8, 4 + state.studio_tier)
    existing = {member.name for member in state.roster}
    market: List[Staff] = []
    role_choices = ["translator", "male", "female", "male", "female"]
    for _ in range(count):
        role_type = rnd.choice(role_choices)
        rarity = _pick_rarity(rnd, state.studio_tier, state.level())
        rarity_mult = RARITY_MULTIPLIER[rarity]
        base_skill = rnd.randint(44, 62) + state.studio_tier * 2 + min(8, state.level())
        base_speed = rnd.randint(42, 64) + state.studio_tier * 2 + min(6, state.day // 2)
        level = 1 + min(4, (state.studio_tier - 1) + rnd.randint(0, max(1, state.level() // 2)))
        if role_type == "translator":
            base_skill += 4
        skill = min(97, int(round(base_skill * (1 + (rarity_mult - 1) * 0.55))))
        speed = min(94, int(round(base_speed * (1 + (rarity_mult - 1) * 0.45))))
        hire_cost = _hire_cost_for(role_type, skill, speed, level, rarity=rarity)
        salary = _salary_for(hire_cost, level, rarity=rarity)
        raw_name = rnd.choice(_all_name_pool(role_type))
        name = _unique_name(raw_name, existing)
        existing.add(name)
        market.append(
            Staff(
                name=name,
                role_type=role_type,
                skill=skill,
                speed=speed,
                energy=100,
                level=level,
                hire_cost=hire_cost,
                salary=salary,
                source="market",
                rarity=rarity,
                traits=_pick_traits(role_type, rnd, rarity),
                burnout=0,
            )
        )
    market.sort(key=lambda member: (RARITY_MULTIPLIER.get(member.rarity, 1.0), member.hire_cost, member.power()), reverse=True)
    return market


def _achievement_progress(state: GameState, achievement_id: str) -> tuple[int, int]:
    if achievement_id == "first_win":
        return state.wins, 1
    if achievement_id == "team_builder":
        return len(state.roster), 15
    if achievement_id == "trainer":
        return state.total_trainings, 3
    if achievement_id == "burnout_clinic":
        return state.total_rests, 4
    if achievement_id == "rep_star":
        return state.reputation, 20
    if achievement_id == "studio_tier_2":
        return state.studio_tier, 2
    return 0, 1


def _achievement_ready(state: GameState, achievement_id: str) -> bool:
    current, target = _achievement_progress(state, achievement_id)
    return current >= target


def _grant_achievements(state: GameState) -> List[str]:
    unlocked: List[str] = []
    for achievement_id in ACHIEVEMENT_ORDER:
        if achievement_id in state.achievements:
            continue
        if not _achievement_ready(state, achievement_id):
            continue
        meta = ACHIEVEMENT_META[achievement_id]
        state.achievements.append(achievement_id)
        state.coins += int(meta["coins"])
        state.xp += int(meta["xp"])
        unlocked.append(achievement_id)
        state.log.append(
            f"Achievement unlock: {meta['title']} (+{meta['coins']} coins, +{meta['xp']} XP)."
        )
    return unlocked


def _rest_cost(member: Staff) -> int:
    rarity_factor = 1 + max(0.0, RARITY_MULTIPLIER.get(member.rarity, 1.0) - 1.0) * 0.6
    return max(6, int(round((8 + member.level * 2 + member.salary // 2) * rarity_factor)))


def _training_cost(member: Staff, focus: str) -> int:
    rarity_factor = 1 + max(0.0, RARITY_MULTIPLIER.get(member.rarity, 1.0) - 1.0) * 0.45
    premium = 2 if focus != "balanced" else 0
    return max(12, int(round((18 + member.level * 5 + premium) * rarity_factor)))


def refresh_market(state: GameState, seed: Optional[int] = None) -> List[Staff]:
    state.market = generate_market(state, seed=seed)
    state.log.append(f"Market refresh untuk hari {state.day}. {len(state.market)} recruit muncul.")
    return state.market


def new_game(user_id: int, studio_name: str = "Studio Baru") -> GameState:
    state = GameState(
        user_id=user_id,
        studio_name=studio_name,
        roster=_make_default_roster(),
        reputation=10,
        inventory={"focus_notes": 1, "wave_mic": 1},
    )
    refresh_market(state)
    state.log.append("Studio dibuka. Misi pertama menunggu.")
    state.log.append("Starter gear diterima: Focus Notes dan Wave Mic.")
    return state


def _next_code(day: int, lang: str, counter: int = 1) -> str:
    return f"{lang.upper()}-{260300 + day:06d}-{counter:02d}"


def _mission_title(rnd: random.Random) -> str:
    return f"{rnd.choice(TITLE_PARTS_A)} {rnd.choice(TITLE_PARTS_B)}"


def generate_mission(state: GameState, seed: Optional[int] = None) -> Mission:
    rnd = random.Random(seed if seed is not None else (state.user_id * 1000 + state.day * 17 + state.level() + state.reputation * 7))
    priority = rnd.choices(PRIORITIES, weights=[10, 30, 35, 25], k=1)[0]
    lang = rnd.choice(LANGS)
    client = _client_for_state(state, rnd)
    year = rnd.randint(2018, 2026)
    role_count = rnd.randint(2, min(5, 2 + state.level() + max(0, state.studio_tier - 1)))
    roles: List[RoleSlot] = []
    for idx in range(1, role_count + 1):
        gender = rnd.choice(["male", "female"])
        prefix = "man" if gender == "male" else "fem"
        lines = rnd.randint(40, 180) + state.level() * rnd.randint(5, 20)
        roles.append(RoleSlot(role=f"{prefix}{idx}", lines=lines, gender=gender))

    reward = 60 + sum(r.lines for r in roles) // 5 + (10 * state.level()) + state.studio_tier * 6
    reward = int(round(reward * client["reward_mult"]))
    xp = 25 + len(roles) * 8 + (15 if priority == "superurgent" else 0) + client["xp_bonus"]
    deadline_day = state.day + PRIORITY_DEADLINES[priority]
    translator_difficulty = 45 + len(roles) * 6 + (15 if priority in {"superurgent", "urgent"} else 0)
    qa_threshold = 55 + len(roles) * 5 + (10 if priority == "superurgent" else 0)
    mission = Mission(
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
        client_name=client["name"],
        client_tier=client["tier"],
        reputation_reward=client["rep"],
        modifiers=_pick_mission_modifiers(state, rnd, priority=priority, client_tier=client["tier"]),
    )
    mission = _apply_modifier_package(mission)
    _remember_client(state, mission.client_name)
    return mission


def ensure_mission(state: GameState) -> Mission:
    if state.current_mission is None:
        state.current_mission = generate_mission(state)
    _remember_client(state, state.current_mission.client_name)
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
        best_translator = max(
            translator_pool,
            key=lambda s: s.power() + _trait_bonus(s, mission, translator=True) - _burnout_penalty(s),
        )
        mission.assigned_translator = best_translator.name
        picks["translator"] = best_translator.name

    used = set()
    for role in mission.roles:
        pool = [s for s in state.roster if s.role_type == role.gender and s.energy > 0 and s.name not in used]
        if not pool:
            continue
        best = max(pool, key=lambda s: s.power() + _trait_bonus(s, mission, role=role) - _burnout_penalty(s))
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
    state.log.append(f"Terima misi {mission.code} untuk client {mission.client_name}.")
    return mission


def _translator_score(state: GameState, mission: Mission) -> float:
    if not mission.assigned_translator:
        return 0.0
    tr = find_staff(state, mission.assigned_translator)
    if not tr:
        return 0.0
    bonus = state.upgrades.get("translator_lab", 0) * 2.5
    return (
        tr.skill * 0.9
        + tr.speed * 0.6
        + tr.level * 5
        + bonus
        + _trait_bonus(tr, mission, translator=True)
        + _equipment_bonus(tr, mission, translator=True)
        - max(0, mission.translator_difficulty - tr.skill) * 0.4
        - _burnout_penalty(tr)
        - _modifier_penalty(tr, mission, translator=True)
    )


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
        total += (
            vo.skill * 0.85
            + vo.speed * 0.5
            + vo.level * 4
            + booth_bonus
            + _trait_bonus(vo, mission, role=role)
            + _equipment_bonus(vo, mission, role=role)
            - _burnout_penalty(vo)
            - _modifier_penalty(vo, mission, translator=False)
        ) / role_weight
    return total / len(mission.roles)


def _deadline_penalty(state: GameState, mission: Mission) -> float:
    late_days = max(0, state.day - mission.deadline_day)
    return late_days * 12.0


def _consume_energy(state: GameState, mission: Mission) -> None:
    names = set(mission.assigned_roles.values())
    if mission.assigned_translator:
        names.add(mission.assigned_translator)
    urgent_load = 4 if mission.priority in {"urgent", "superurgent"} else 0
    for member in state.roster:
        if member.name in names:
            drain = 18 + urgent_load
            if "workhorse" in member.traits:
                drain -= 4
            member.energy = max(10, member.energy - drain)
            burnout_gain = 0
            if member.energy <= 55:
                burnout_gain += 8
            if member.energy <= 35:
                burnout_gain += 7
            if mission.client_tier in {"premium", "enterprise"}:
                burnout_gain += 2
            if "resilient" in member.traits:
                burnout_gain = max(0, burnout_gain - 4)
            member.burnout = min(100, member.burnout + burnout_gain)
        else:
            recover = 8 + state.upgrades.get("lounge", 0) * 2
            member.energy = min(100, member.energy + recover)
            member.burnout = max(0, member.burnout - (5 + state.upgrades.get("lounge", 0) * 2))


def _grant_inventory_item(state: GameState, item_key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    state.inventory[item_key] = int(state.inventory.get(item_key, 0)) + amount


def _roll_chest_loot(state: GameState, mission: Mission, qa_score: float, passed: bool) -> List[str]:
    if not passed:
        return []
    rnd = random.Random(state.user_id * 97 + state.day * 31 + state.wins * 13 + len(mission.code) + int(qa_score))
    chest_chance = 0.35
    if mission.client_tier in {"premium", "enterprise"}:
        chest_chance += 0.12
    if qa_score >= mission.qa_threshold + 12:
        chest_chance += 0.10
    if rnd.random() > chest_chance:
        return []
    pool = list(EQUIPMENT_CATALOG.keys())
    weights = []
    for key in pool:
        weight = 12
        if key in {"rush_kit", "glossary_pad"} and mission.priority in {"urgent", "superurgent"}:
            weight += 6
        if key == "pop_filter" and "lip_sync_heavy" in mission.modifiers:
            weight += 8
        if key == "calm_tea":
            weight += 4
        weights.append(weight)
    item = rnd.choices(pool, weights=weights, k=1)[0]
    _grant_inventory_item(state, item, 1)
    state.total_chests += 1
    return [item]


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
    rep_change = mission.reputation_reward if passed else -max(1, mission.reputation_reward)
    if mission.client_tier == "enterprise" and passed:
        rep_change += 1

    state.coins += reward
    state.xp += gained_xp
    state.reputation = max(0, state.reputation + rep_change)
    state.wins += 1 if passed else 0
    state.losses += 0 if passed else 1
    _consume_energy(state, mission)

    loot = _roll_chest_loot(state, mission, qa_score=qa_score, passed=passed)
    if passed:
        loot_text = f" Loot: {', '.join(_equipment_label(item) for item in loot)}." if loot else ""
        state.log.append(
            f"Misi {mission.code} lulus QA untuk {mission.client_name}. +{reward} coins, +{gained_xp} XP, rep {rep_change:+d}.{loot_text}"
        )
    else:
        state.log.append(
            f"Misi {mission.code} gagal QA untuk {mission.client_name}. +{reward} coins saguhati, +{gained_xp} XP, rep {rep_change:+d}"
        )

    unlocked = _grant_achievements(state)

    result = {
        "passed": passed,
        "qa_score": round(qa_score, 1),
        "threshold": mission.qa_threshold,
        "reward": reward,
        "xp": gained_xp,
        "code": mission.code,
        "title": mission.title,
        "reputation": state.reputation,
        "rep_change": rep_change,
        "client_name": mission.client_name,
        "client_tier": mission.client_tier,
        "achievements": unlocked,
        "loot": loot,
        "modifiers": list(mission.modifiers),
    }
    state.current_mission = None
    return result


def inventory_summary(state: GameState) -> str:
    lines = [
        f"🎒 Inventory — {state.studio_name}",
        f"Coins {state.coins} | Total chests {state.total_chests}",
    ]
    if not state.inventory:
        lines.append("- inventory kosong")
        return "\n".join(lines)
    for key, qty in sorted(state.inventory.items(), key=lambda item: (_equipment_meta(item[0]).get('cost', 0), item[0]), reverse=True):
        if qty <= 0:
            continue
        meta = _equipment_meta(key)
        lines.append(f"- {_equipment_label(key)} x{qty} | roles {', '.join(meta.get('roles', []))} | {meta.get('desc', '-')}")
    return "\n".join(lines)


def gear_shop_summary(state: GameState) -> str:
    lines = [f"🧰 Gear shop — Coins {state.coins}"]
    for key, meta in EQUIPMENT_CATALOG.items():
        lines.append(f"- {meta['label']} ({key}) | cost {meta['cost']} | roles {', '.join(meta['roles'])} | {meta['desc']}")
    return "\n".join(lines)


def buy_gear(state: GameState, item_key: str) -> Dict[str, object]:
    key = (item_key or '').strip().lower()
    meta = _equipment_meta(key)
    if not meta:
        raise ValueError("Gear tak wujud.")
    cost = int(meta.get('cost', 0))
    if state.coins < cost:
        raise ValueError(f"Coins tak cukup. Perlu {cost}.")
    state.coins -= cost
    _grant_inventory_item(state, key, 1)
    state.log.append(f"Beli gear {_equipment_label(key)} dengan kos {cost}.")
    return {"item_key": key, "label": _equipment_label(key), "cost": cost, "qty": state.inventory.get(key, 0)}


def equip_gear(state: GameState, staff_name: str, item_key: str) -> Dict[str, object]:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    key = (item_key or '').strip().lower()
    meta = _equipment_meta(key)
    if not meta:
        raise ValueError("Gear tak wujud.")
    if state.inventory.get(key, 0) <= 0:
        raise ValueError("Item tu tak ada dalam inventory.")
    if member.role_type not in set(meta.get('roles', [])):
        raise ValueError("Gear tu tak sesuai untuk role staff ini.")
    previous = member.equipped
    if previous:
        _grant_inventory_item(state, previous, 1)
    member.equipped = key
    state.inventory[key] = int(state.inventory.get(key, 0)) - 1
    if state.inventory[key] <= 0:
        state.inventory.pop(key, None)
    state.log.append(f"{member.name} equip {_equipment_label(key)}.")
    return {"member": member, "item_key": key, "label": _equipment_label(key), "previous": previous}


def unequip_gear(state: GameState, staff_name: str) -> Dict[str, object]:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    if not member.equipped:
        raise ValueError("Staff ini belum equip gear.")
    previous = member.equipped
    member.equipped = None
    _grant_inventory_item(state, previous, 1)
    state.log.append(f"{member.name} unequip {_equipment_label(previous)}.")
    return {"member": member, "item_key": previous, "label": _equipment_label(previous)}


def hire_staff(state: GameState, staff_name: str) -> Staff:
    candidate = find_market_staff(state, staff_name)
    if not candidate:
        raise ValueError("Staff market tak jumpa.")
    if state.coins < candidate.hire_cost:
        raise ValueError(f"Coins tak cukup. Perlu {candidate.hire_cost}.")
    state.coins -= candidate.hire_cost
    state.roster.append(candidate)
    state.market = [member for member in state.market if member.name.lower() != candidate.name.lower()]
    state.log.append(f"Hire {candidate.name} [{candidate.role_type}] {candidate.rarity} dengan kos {candidate.hire_cost}.")
    _grant_achievements(state)
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
        state.reputation += 1
        refresh_market(state)
        state.log.append(f"Studio expand ke tier {state.studio_tier}. Kos {cost}.")
        unlocked = _grant_achievements(state)
        return {"target": "studio", "cost": cost, "level": state.studio_tier, "unlocked": unlocked}

    if normalized == "translator_lab":
        state.upgrades["translator_lab"] = state.upgrades.get("translator_lab", 0) + 1
        for member in state.roster:
            if member.role_type == "translator":
                member.skill = min(99, member.skill + 3)
                member.speed = min(95, member.speed + 1)
        state.log.append(f"Translator Lab naik ke lvl {state.upgrades['translator_lab']}. Kos {cost}.")
        unlocked = _grant_achievements(state)
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized], "unlocked": unlocked}

    if normalized == "vo_booth":
        state.upgrades["vo_booth"] = state.upgrades.get("vo_booth", 0) + 1
        for member in state.roster:
            if member.role_type in {"male", "female"}:
                member.skill = min(99, member.skill + 3)
                member.speed = min(95, member.speed + 1)
        state.log.append(f"VO Booth naik ke lvl {state.upgrades['vo_booth']}. Kos {cost}.")
        unlocked = _grant_achievements(state)
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized], "unlocked": unlocked}

    if normalized == "lounge":
        state.upgrades["lounge"] = state.upgrades.get("lounge", 0) + 1
        for member in state.roster:
            member.energy = min(100, member.energy + 12)
            member.burnout = max(0, member.burnout - 6)
        state.log.append(f"Lounge naik ke lvl {state.upgrades['lounge']}. Kos {cost}.")
        unlocked = _grant_achievements(state)
        return {"target": normalized, "cost": cost, "level": state.upgrades[normalized], "unlocked": unlocked}

    raise ValueError("Upgrade tak dikenali.")


def train_staff(state: GameState, staff_name: str, focus: str = "balanced") -> Dict[str, object]:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    focus_key = (focus or "balanced").strip().lower()
    if focus_key not in TRAINING_FOCUS:
        raise ValueError("Focus training mesti balanced, skill, atau speed.")
    cost = _training_cost(member, focus_key)
    if state.coins < cost:
        raise ValueError(f"Coins tak cukup. Perlu {cost}.")

    gains = dict(TRAINING_FOCUS[focus_key])
    if focus_key != "speed" and member.role_type == "translator" and "polyglot" in member.traits:
        gains["skill"] += 1
    if focus_key != "skill" and "sprinter" in member.traits:
        gains["speed"] += 1
    if member.role_type in {"male", "female"} and "natural" in member.traits and focus_key != "speed":
        gains["skill"] += 1
    if "resilient" in member.traits:
        gains["burnout"] = max(1, gains["burnout"] - 2)
    if "workhorse" in member.traits:
        gains["energy"] = max(6, gains["energy"] - 2)

    state.coins -= cost
    member.skill = min(99, member.skill + gains["skill"])
    member.speed = min(99, member.speed + gains["speed"])
    member.energy = max(12, member.energy - gains["energy"])
    member.burnout = min(100, member.burnout + gains["burnout"])
    member.training_sessions += 1
    state.total_trainings += 1
    level_up = False
    if member.training_sessions % 3 == 0:
        member.level = min(99, member.level + 1)
        level_up = True
    unlocked = _grant_achievements(state)
    state.log.append(
        f"Training {focus_key} untuk {member.name}. Kos {cost}. Skill +{gains['skill']} / Speed +{gains['speed']}."
    )
    return {
        "member": member,
        "focus": focus_key,
        "cost": cost,
        "level_up": level_up,
        "unlocked": unlocked,
    }


def rest_staff(state: GameState, staff_name: str) -> Dict[str, object]:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    cost = _rest_cost(member)
    if state.coins < cost:
        raise ValueError(f"Coins tak cukup. Perlu {cost}.")

    energy_gain = 26 + state.upgrades.get("lounge", 0) * 4
    burnout_drop = 18 + state.upgrades.get("lounge", 0) * 4
    if "workhorse" in member.traits:
        energy_gain += 3
    if "resilient" in member.traits:
        burnout_drop += 4

    before_energy = member.energy
    before_burnout = member.burnout
    state.coins -= cost
    member.energy = min(100, member.energy + energy_gain)
    member.burnout = max(0, member.burnout - burnout_drop)
    state.total_rests += 1
    unlocked = _grant_achievements(state)
    state.log.append(f"Rest session untuk {member.name}. Kos {cost}.")
    return {
        "member": member,
        "cost": cost,
        "energy_recovered": member.energy - before_energy,
        "burnout_reduced": before_burnout - member.burnout,
        "unlocked": unlocked,
    }


def rest_all_staff(state: GameState) -> Dict[str, object]:
    if not state.roster:
        raise ValueError("Roster kosong.")
    raw_cost = sum(_rest_cost(member) for member in state.roster)
    total_cost = max(12, int(round(raw_cost * 0.72)))
    if state.coins < total_cost:
        raise ValueError(f"Coins tak cukup. Perlu {total_cost}.")
    state.coins -= total_cost

    total_energy = 0
    total_burnout = 0
    for member in state.roster:
        energy_gain = 22 + state.upgrades.get("lounge", 0) * 4
        burnout_drop = 16 + state.upgrades.get("lounge", 0) * 4
        if "workhorse" in member.traits:
            energy_gain += 2
        if "resilient" in member.traits:
            burnout_drop += 3
        before_energy = member.energy
        before_burnout = member.burnout
        member.energy = min(100, member.energy + energy_gain)
        member.burnout = max(0, member.burnout - burnout_drop)
        total_energy += member.energy - before_energy
        total_burnout += before_burnout - member.burnout
        state.total_rests += 1

    unlocked = _grant_achievements(state)
    state.log.append(f"Company rest day dijalankan. Kos {total_cost}.")
    return {
        "cost": total_cost,
        "energy_recovered": total_energy,
        "burnout_reduced": total_burnout,
        "unlocked": unlocked,
    }


def next_day(state: GameState) -> None:
    state.day += 1
    payroll = total_salary(state)
    had_payroll_shortage = state.coins < payroll
    state.coins = max(0, state.coins - payroll)
    recovery = 12 + state.upgrades.get("lounge", 0) * 4
    burnout_recovery = 9 + state.upgrades.get("lounge", 0) * 3
    for member in state.roster:
        member.energy = min(100, member.energy + recovery)
        member.burnout = max(0, member.burnout - burnout_recovery)
        if had_payroll_shortage:
            member.energy = max(10, member.energy - 8)
            member.burnout = min(100, member.burnout + 5)
    refresh_market(state)
    if had_payroll_shortage:
        state.reputation = max(0, state.reputation - 1)
        state.log.append(f"Hari {state.day} bermula. Payroll {payroll} tak cukup, morale jatuh dan rep -1.")
    else:
        state.log.append(f"Hari {state.day} bermula. Payroll dibayar: {payroll}.")
    _grant_achievements(state)


def _assigned_staff_names(mission: Mission) -> set[str]:
    names = set(mission.assigned_roles.values())
    if mission.assigned_translator:
        names.add(mission.assigned_translator)
    return names


def assigned_staff_members(state: GameState, mission: Optional[Mission] = None) -> List[Staff]:
    mission = mission or ensure_mission(state)
    names = _assigned_staff_names(mission)
    return [member for member in state.roster if member.name in names]


def submission_risk_report(state: GameState) -> Dict[str, object]:
    mission = ensure_mission(state)
    warnings: List[str] = []
    blockers: List[str] = []
    if not mission.accepted:
        blockers.append("Terima mission dulu.")
    if not mission.assigned_translator:
        blockers.append("Translator belum assign.")
    missing = [role.role for role in mission.roles if role.role not in mission.assigned_roles]
    if missing:
        blockers.append("Role belum assign: " + ", ".join(missing))

    risky_members: List[Dict[str, object]] = []
    for member in assigned_staff_members(state, mission):
        level = "ok"
        if member.energy <= 25 or member.burnout >= 70:
            level = "critical"
        elif member.energy <= 40 or member.burnout >= 45:
            level = "warn"
        if level != "ok":
            risky_members.append({
                "name": member.name,
                "role_type": member.role_type,
                "energy": member.energy,
                "burnout": member.burnout,
                "level": level,
            })

    if risky_members:
        critical = [m for m in risky_members if m["level"] == "critical"]
        warn = [m for m in risky_members if m["level"] == "warn"]
        if critical:
            warnings.append("Critical burnout / low energy detected.")
        if warn:
            warnings.append("Some assigned staff are tired and may fail QA.")

    return {
        "blockers": blockers,
        "warnings": warnings,
        "risky_members": risky_members,
        "has_warning": bool(warnings),
        "can_submit": not blockers,
    }


def _staff_line(member: Staff, compact: bool = False) -> str:
    rarity_icon = RARITY_ICON.get(member.rarity, "⚪")
    traits = _format_traits(member)
    if compact:
        gear = _equipment_label(member.equipped) if member.equipped else '-'
        return (
            f"{rarity_icon} {member.name}: power {round(member.power(), 1)}, energy {member.energy}, "
            f"burnout {member.burnout}, lvl {member.level}, gear {gear}, train {member.training_sessions}, traits {traits}"
        )
    gear = _equipment_label(member.equipped) if member.equipped else '-'
    return (
        f"- {rarity_icon} {member.name}: skill {member.skill}, speed {member.speed}, energy {member.energy}, "
        f"burnout {member.burnout}, lvl {member.level}, gear {gear}, train {member.training_sessions}, rarity {member.rarity}, salary {member.salary}, traits {traits}"
    )


def roster_summary(state: GameState) -> str:
    lines = [
        f"👥 Roster — {state.studio_name}",
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()} | Rep {state.reputation}",
        f"Studio tier {state.studio_tier} | Payroll {total_salary(state)} | Upgrades T{state.upgrades.get('translator_lab', 0)} / V{state.upgrades.get('vo_booth', 0)} / L{state.upgrades.get('lounge', 0)}",
    ]
    for kind, label in [("translator", "Translator"), ("male", "VO Male"), ("female", "VO Female")]:
        members = [s for s in state.roster if s.role_type == kind]
        lines.append(f"\n{label} ({len(members)}):")
        if not members:
            lines.append("- kosong")
            continue
        for member in members:
            lines.append(_staff_line(member))
    return "\n".join(lines)


def current_team_summary(state: GameState) -> str:
    mission = ensure_mission(state)
    lines = [
        f"🎯 Team untuk mission {mission.code}",
        f"{mission.title}",
        f"Client: {mission.client_name} [{mission.client_tier}] | Rep +{mission.reputation_reward}",
        f"Modifiers: {', '.join(_modifier_label(mod) for mod in mission.modifiers) if mission.modifiers else '-'}\n"
        "",
        f"Translator: {mission.assigned_translator or '-'}",
    ]
    for role in mission.roles:
        lines.append(f"- {role.role}: {mission.assigned_roles.get(role.role, '-')} ({role.lines} lines)")
    assigned_names = _assigned_staff_names(mission)
    lines.append("")
    if assigned_names:
        lines.append("Staff on mission:")
        for member in sorted((s for s in state.roster if s.name in assigned_names), key=lambda s: (s.role_type, s.name.lower())):
            lines.append(f"- {_staff_line(member, compact=True)}")
    else:
        lines.append("Belum ada staff assign lagi.")
    return "\n".join(lines)


def bench_summary(state: GameState) -> str:
    mission = ensure_mission(state)
    assigned_names = _assigned_staff_names(mission)
    lines = [f"🪑 Bench untuk mission {mission.code}", "Staff yang belum digunakan untuk mission semasa:"]
    for kind, label in [("translator", "Translator bench"), ("male", "VO Male bench"), ("female", "VO Female bench")]:
        lines.append(f"\n{label}:")
        bench = [s for s in state.roster if s.role_type == kind and s.name not in assigned_names]
        if not bench:
            lines.append("- kosong")
            continue
        bench = sorted(bench, key=lambda s: (s.energy - s.burnout, s.power(), s.level), reverse=True)
        for member in bench:
            lines.append(f"- {_staff_line(member, compact=True)}")
    return "\n".join(lines)


def market_summary(state: GameState) -> str:
    if not state.market:
        return "🛒 Market kosong. Guna /nextday untuk refresh."
    lines = [
        f"🛒 Recruitment market — Day {state.day}",
        f"Studio tier {state.studio_tier} | Reputation {state.reputation} | Coins {state.coins}",
    ]
    for kind, label in [("translator", "Translator recruits"), ("male", "VO Male recruits"), ("female", "VO Female recruits")]:
        lines.append(f"\n{label}:")
        items = [s for s in state.market if s.role_type == kind]
        if not items:
            lines.append("- kosong")
            continue
        items = sorted(items, key=lambda s: (RARITY_MULTIPLIER.get(s.rarity, 1.0), s.hire_cost, s.power()), reverse=True)
        for member in items:
            rarity_icon = RARITY_ICON.get(member.rarity, "⚪")
            lines.append(
                f"- {rarity_icon} {member.name} | rarity {member.rarity} | power {round(member.power(), 1)} | hire {member.hire_cost} | salary {member.salary} | traits {_format_traits(member)}"
            )
    return "\n".join(lines)


def studio_summary(state: GameState) -> str:
    translator_cost = _upgrade_cost(state, "translator_lab")
    vo_cost = _upgrade_cost(state, "vo_booth")
    lounge_cost = _upgrade_cost(state, "lounge")
    studio_cost = _upgrade_cost(state, "studio")
    unlocked_tiers = ", ".join(sorted({c["tier"] for c in CLIENT_POOL if state.reputation >= CLIENT_UNLOCK_REP[c["tier"]]}))
    return (
        f"🏢 Studio panel — {state.studio_name}\n"
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()}\n"
        f"Wins {state.wins} | Losses {state.losses} | Reputation {state.reputation}\n"
        f"Studio tier {state.studio_tier} | Payroll per day {total_salary(state)}\n"
        f"Roster {len(state.roster)} | Market {len(state.market)} | Achievements {len(state.achievements)}/{len(ACHIEVEMENT_ORDER)}\n"
        f"Unlocked client tiers: {unlocked_tiers or 'indie'}\n\n"
        f"Upgrades:\n"
        f"- translator_lab lvl {state.upgrades.get('translator_lab', 0)} (next {translator_cost})\n"
        f"- vo_booth lvl {state.upgrades.get('vo_booth', 0)} (next {vo_cost})\n"
        f"- lounge lvl {state.upgrades.get('lounge', 0)} (next {lounge_cost})\n"
        f"- studio tier {state.studio_tier} (next expansion {studio_cost})"
    )


def mission_summary(mission: Mission) -> str:
    roles = "\n".join(f"- {r.role} ({r.gender}) — {r.lines} lines" for r in mission.roles)
    assigned = [f"Translator: {mission.assigned_translator or '-'}"]
    for r in mission.roles:
        assigned.append(f"{r.role}: {mission.assigned_roles.get(r.role, '-')}")
    return (
        f"🎬 Mission card\n"
        f"{mission.title} ({mission.year})\n"
        f"Code: {mission.code}\n"
        f"Client: {mission.client_name} [{mission.client_tier}]\n"
        f"Lang: {mission.lang.upper()} | Priority: {mission.priority} | Deadline day: {mission.deadline_day}\n"
        f"Reward: {mission.reward} coins | XP: {mission.xp} | Rep: {mission.reputation_reward}\n"
        f"Modifiers: {', '.join(_modifier_label(mod) for mod in mission.modifiers) if mission.modifiers else '-'}\n"
        f"Source: {mission.source}\n\n"
        f"Roles:\n{roles}\n\n"
        f"Assignments:\n" + "\n".join(assigned)
    )

def submission_risk_text(state: GameState) -> str:
    report = submission_risk_report(state)
    mission = ensure_mission(state)
    lines = [f"⚠️ QA risk check — {mission.code}"]
    if report["blockers"]:
        lines.append("Blockers:")
        for item in report["blockers"]:
            lines.append(f"- {item}")
    if report["warnings"]:
        if report["blockers"]:
            lines.append("")
        lines.append("Warnings:")
        for item in report["warnings"]:
            lines.append(f"- {item}")
    risky_members = report.get("risky_members") or []
    if risky_members:
        lines.append("")
        lines.append("Risky staff:")
        for member in risky_members:
            lines.append(
                f"- {member['name']} [{member['role_type']}] energy {member['energy']} | burnout {member['burnout']} | {member['level']}"
            )
    if not report["blockers"] and not report["warnings"]:
        lines.append("All assigned staff look stable for submission.")
    return "\n".join(lines)


def latest_log(state: GameState, limit: int = 8) -> str:
    items = state.log[-limit:]
    return "\n".join(f"- {item}" for item in items) if items else "- Tiada log"


def client_summary(state: GameState) -> str:
    current = ensure_mission(state)
    lines = [
        f"🤝 Client desk — Reputation {state.reputation}",
        f"Current mission: {current.client_name} [{current.client_tier}] | reward {current.reward} coins | rep +{current.reputation_reward}",
        "",
        "Unlocked / known clients:",
    ]
    visible = 0
    for client in CLIENT_POOL:
        if client["name"] in state.clients_seen or state.reputation >= CLIENT_UNLOCK_REP[client["tier"]]:
            visible += 1
            lines.append(
                f"- {client['name']} [{client['tier']}] — reward x{client['reward_mult']} | rep +{client['rep']}"
            )
    if visible == 0:
        lines.append("- Belum ada client direkod.")
    return "\n".join(lines)


def reputation_summary(state: GameState) -> str:
    unlocked = [client["name"] for client in CLIENT_POOL if state.reputation >= CLIENT_UNLOCK_REP[client["tier"]]]
    next_tier = next((tier for tier, need in sorted(CLIENT_UNLOCK_REP.items(), key=lambda item: item[1]) if state.reputation < need), None)
    return (
        f"⭐ Reputation board\n"
        f"Studio: {state.studio_name}\n"
        f"Reputation: {state.reputation}\n"
        f"Wins/Losses: {state.wins}/{state.losses}\n"
        f"Unlocked clients: {', '.join(unlocked) if unlocked else 'Indie Spark'}\n"
        f"Achievements: {len(state.achievements)}/{len(ACHIEVEMENT_ORDER)}\n"
        f"Next tier target: {next_tier or 'max tier unlocked'}"
    )

def staff_detail_summary(state: GameState, staff_name: str) -> str:
    member = find_staff(state, staff_name)
    if not member:
        raise ValueError("Staff tak wujud.")
    assigned = False
    mission = state.current_mission
    if mission is not None:
        assigned = member.name == mission.assigned_translator or member.name in mission.assigned_roles.values()
    return (
        f"🧾 Staff card\n"
        f"Name: {member.name}\n"
        f"Role: {member.role_type}\n"
        f"Rarity: {member.rarity} {RARITY_ICON.get(member.rarity, '⚪')}\n"
        f"Skill {member.skill} | Speed {member.speed} | Level {member.level}\n"
        f"Energy {member.energy} | Burnout {member.burnout} | Train sessions {member.training_sessions}\n"
        f"Salary/day {member.salary} | Assigned now: {'yes' if assigned else 'no'}\n"
        f"Equipped: {_equipment_label(member.equipped) if member.equipped else '-'}\n"
        f"Traits: {_format_traits(member)}\n"
        f"Train cost: {_training_cost(member, 'balanced')} | Rest cost: {_rest_cost(member)}"
    )


def goals_summary(state: GameState) -> str:
    unlocked_ids = list(state.achievements)
    lines = [
        f"🏆 Goals board — {state.studio_name}",
        f"Unlocked {len(unlocked_ids)}/{len(ACHIEVEMENT_ORDER)} achievements",
        f"Wins {state.wins} | Reputation {state.reputation} | Trainings {state.total_trainings} | Rests {state.total_rests}",
        "",
        "Unlocked:",
    ]
    if unlocked_ids:
        for achievement_id in unlocked_ids:
            meta = ACHIEVEMENT_META[achievement_id]
            lines.append(f"- ✅ {meta['title']} — {meta['desc']}")
    else:
        lines.append("- belum ada")

    lines.append("")
    lines.append("Next milestones:")
    pending = [achievement_id for achievement_id in ACHIEVEMENT_ORDER if achievement_id not in unlocked_ids]
    if not pending:
        lines.append("- Semua achievement utama dah unlock.")
    else:
        for achievement_id in pending[:3]:
            meta = ACHIEVEMENT_META[achievement_id]
            current, target = _achievement_progress(state, achievement_id)
            lines.append(
                f"- {meta['title']} — {meta['desc']} ({current}/{target}) → +{meta['coins']} coins, +{meta['xp']} XP"
            )
    return "\n".join(lines)


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
        reputation=data.get("reputation", 10),
        clients_seen=data.get("clients_seen", []),
        achievements=data.get("achievements", []),
        total_trainings=data.get("total_trainings", 0),
        total_rests=data.get("total_rests", 0),
        inventory=data.get("inventory", {}) or {},
        total_chests=data.get("total_chests", 0),
    )
    if state.current_mission is not None:
        _remember_client(state, state.current_mission.client_name)
    if not state.market:
        refresh_market(state)
    return state
