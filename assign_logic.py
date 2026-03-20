import re
from typing import List, Tuple, Optional
from models import VOTeam, Assignment

LEVEL_MULT = {
    "expert_old": 0.85,
    "trained_new": 1.00,
    "new_limited": 1.25,
}

SPEED_MULT = {
    "normal": 1.00,
    "slow": 1.20,
}


def norm_role(s: str) -> Optional[str]:
    s = (s or "").strip().lower()
    s = s.replace(" ", "").replace("_", "")
    s = s.replace("—", "-").replace("–", "-")
    # allow: man-1, man1, male-1, female2, fem-12
    m = re.match(r"^(man|male|m|fem|female|f)[-]?([0-9]{1,2})$", s)
    if not m:
        return None
    prefix = m.group(1)
    num = int(m.group(2))
    if num <= 0:
        return None
    if prefix in ("man", "male", "m"):
        return f"man{num}"
    return f"fem{num}"


def parse_lines(text: str) -> List[Tuple[str, int]]:
    """Parse role/line blocks.

    Input:
      man-1 120
      fem-2 98
    Output:
      [("man1", 120), ("fem2", 98)]
    """
    # IMPORTANT: In "Option A" we treat each role bucket (man1/man2/fem1/...) as a single unit.
    # So if the rolelist contains multiple character rows under the same bucket
    # (e.g. "man-1 Bugs 695" and "man-1 Male1 6"), we SUM them and return ONE entry:
    #   man1 -> 701
    # This ensures: 1 role bucket = 1 VO.
    totals: dict[str, int] = {}
    order: List[str] = []
    for raw in (text or "").splitlines():
        t = raw.strip()
        if not t:
            continue

        parts = re.split(r"\s+", t)
        role = None
        # try first token
        if parts:
            role = norm_role(parts[0])
        # try two-token role: "man 1 120"
        if not role and len(parts) >= 2:
            role = norm_role(parts[0] + parts[1])
        if not role:
            continue

        nums = re.findall(r"(\d+)", t)
        if not nums:
            continue

        lines = int(nums[-1])
        if role not in totals:
            totals[role] = 0
            order.append(role)
        totals[role] += lines

    return [(r, totals[r]) for r in order]


def role_gender(role: str) -> str:
    return "male" if role.startswith("man") else "female"


def movie_load(project: str):
    """Return dict: {vo_name: total_lines_in_movie} (existing assignments in same project)"""
    rows = Assignment.query.filter_by(project=project).all()
    load = {}
    for r in rows:
        load[r.vo] = load.get(r.vo, 0) + (r.lines or 0)
    return load


def pick_vo(candidates, used: set, load: dict, project_counts: dict | None = None):
    """Pick lowest weighted VO not used yet.

    project_counts:
      {vo_name: number_of_distinct_projects_assigned}

    Business rule (Feb 2026):
      Prefer Expert + Urgent_OK *only* if that VO currently has <= 2 movies.
      If they already have 3+ movies, deprioritize them.
    """
    best = None
    best_score = None

    project_counts = project_counts or {}

    for v in candidates:
        if v.name in used:
            continue

        base = load.get(v.name, 0)
        mult = LEVEL_MULT.get(v.level, 1.0) * SPEED_MULT.get(v.speed, 1.0)
        score = base * mult

        # Prefer "expert + urgent_ok" only when their current workload (movies) is small.
        # This is intentionally a soft preference (still allows falling back to others).
        movie_count = int(project_counts.get(v.name, 0) or 0)
        is_expert = (v.level or "").lower() in {"expert_old", "expert", "senior"}
        if is_expert and getattr(v, "urgent_ok", False):
            if movie_count >= 3:
                score += 10_000_000  # effectively avoid unless no alternatives
            else:
                score *= 0.65  # make them more likely to be picked

        if best is None or score < best_score:
            best = v
            best_score = score

    return best
