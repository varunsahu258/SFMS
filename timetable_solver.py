"""Pure-Python backtracking CSP solver for SFMS automatic timetables."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

MAX_BACKTRACKS = 50_000


@dataclass
class SolverResult:
    success: bool
    slots: list[dict]
    violations: list[str]
    stats: dict


def solve(problem: dict, max_backtracks: int = MAX_BACKTRACKS) -> SolverResult:
    """Solve a self-contained timetable problem using MRV and forward checking."""
    started = perf_counter()
    classes = list(problem.get("classes", ()))
    days = list(problem.get("days", ()))
    periods = [int(value) for value in problem.get("periods", ())]
    teachers = problem.get("teachers", {})
    assignments = problem.get("assignments", {})
    availability = problem.get("availability", {})
    constraints = problem.get("constraints", {})
    requirements = [dict(item) for item in problem.get("requirements", ()) if int(item.get("periods_per_week", 0)) > 0]
    row_slots = [(class_name, day, period) for class_name in classes for day in days for period in periods]
    occupied: dict[tuple[str, str, int], tuple[int, int]] = {}
    teacher_busy: set[tuple[int, str, int]] = set()
    teacher_daily: dict[tuple[int, str], int] = {}
    remaining = {(row["class_name"], int(row["subject_id"])): int(row["periods_per_week"]) for row in requirements}
    requirement_info = {(row["class_name"], int(row["subject_id"])): row for row in requirements}
    backtracks = 0
    best: dict[tuple[str, str, int], tuple[int, int]] = {}
    failure = "No timetable satisfies all hard constraints."

    capacity = len(days) * len(periods)
    for class_name in classes:
        needed = sum(count for (name, _subject), count in remaining.items() if name == class_name)
        if needed > capacity:
            return SolverResult(False, [], [f"{class_name} requires {needed} periods but only {capacity} slots exist."], {"backtracks": 0, "duration_ms": int((perf_counter() - started) * 1000)})
    for key in remaining:
        if not assignments.get(key):
            return SolverResult(False, [], [f"No eligible teacher is assigned for {key[0]} / subject {key[1]}."], {"backtracks": 0, "duration_ms": int((perf_counter() - started) * 1000)})

    def hard_legal(class_name: str, subject_id: int, teacher_id: int, day: str, period: int) -> bool:
        if (class_name, day, period) in occupied or (teacher_id, day, period) in teacher_busy:
            return False
        if period not in availability.get(teacher_id, {}).get(day, []):
            return False
        if constraints.get(teacher_id, {}).get(day, {}).get(period) == "UNAVAILABLE":
            return False
        maximum = int(teachers[teacher_id].get("max_periods_day", 6))
        if teacher_daily.get((teacher_id, day), 0) >= maximum:
            return False
        return teacher_id in assignments.get((class_name, subject_id), ())

    def candidates(key: tuple[str, int]) -> list[tuple[str, int, int]]:
        class_name, subject_id = key
        result = []
        for _class, day, period in row_slots:
            if _class != class_name:
                continue
            for teacher_id in assignments.get(key, ()):
                if hard_legal(class_name, subject_id, teacher_id, day, period):
                    result.append((day, period, teacher_id))
        info = requirement_info[key]

        def score(candidate):
            day, period, teacher_id = candidate
            preference = constraints.get(teacher_id, {}).get(day, {}).get(period)
            same_day_count = sum(1 for (name, used_day, _period), (used_subject, _teacher) in occupied.items() if name == class_name and used_day == day and used_subject == subject_id)
            consecutive = any(occupied.get((class_name, day, adjacent), (None, None))[0] == subject_id for adjacent in (period - 1, period + 1))
            teacher_run = sum(1 for adjacent in range(max(1, period - 4), period) if (teacher_id, day, adjacent) in teacher_busy)
            return (
                1 if preference == "PREFERRED_FREE" else 0,
                0 if preference == "PREFERRED_TEACH" else 1,
                same_day_count,
                1 if consecutive and not int(info.get("double_period_allowed", 0)) else 0,
                teacher_run,
                days.index(day), period,
            )

        result.sort(key=score)
        return result

    def forward_possible() -> bool:
        for key, count in remaining.items():
            if count > 0 and len(candidates(key)) < count:
                return False
        for class_name in classes:
            need = sum(count for (name, _subject), count in remaining.items() if name == class_name)
            free = sum(1 for name, day, period in row_slots if name == class_name and (name, day, period) not in occupied)
            if need > free:
                return False
        return True

    def recurse() -> bool:
        nonlocal backtracks, best, failure
        if len(occupied) > len(best):
            best = dict(occupied)
        unfinished = [key for key, count in remaining.items() if count > 0]
        if not unfinished:
            return True
        if backtracks >= max_backtracks:
            failure = f"Generation stopped after the hard limit of {max_backtracks:,} backtracks. The setup may be over-constrained."
            return False
        ranked = []
        for key in unfinished:
            options = candidates(key)
            ranked.append((len(options), -remaining[key], key, options))
        _size, _negative_count, key, options = min(ranked, key=lambda item: (item[0], item[1], item[2]))
        if not options:
            return False
        class_name, subject_id = key
        for day, period, teacher_id in options:
            slot_key = (class_name, day, period)
            occupied[slot_key] = (subject_id, teacher_id)
            teacher_busy.add((teacher_id, day, period))
            teacher_daily[(teacher_id, day)] = teacher_daily.get((teacher_id, day), 0) + 1
            remaining[key] -= 1
            if forward_possible() and recurse():
                return True
            remaining[key] += 1
            teacher_daily[(teacher_id, day)] -= 1
            teacher_busy.remove((teacher_id, day, period))
            del occupied[slot_key]
            backtracks += 1
            if backtracks >= max_backtracks:
                failure = f"Generation stopped after the hard limit of {max_backtracks:,} backtracks. The setup may be over-constrained."
                break
        return False

    success = recurse()
    chosen = occupied if success else best
    slots = []
    for class_name, day, period in row_slots:
        subject_teacher = chosen.get((class_name, day, period))
        slots.append({
            "class_name": class_name, "day": day, "period_no": period,
            "subject_id": subject_teacher[0] if subject_teacher else None,
            "teacher_id": subject_teacher[1] if subject_teacher else None,
            "is_free": 0 if subject_teacher else 1, "is_locked": 0,
        })
    violations = _soft_violations(slots, problem) if success else [failure]
    return SolverResult(success, slots, violations, {"backtracks": backtracks, "duration_ms": int((perf_counter() - started) * 1000)})


def _soft_violations(slots: list[dict], problem: dict) -> list[str]:
    """Describe soft-constraint compromises in a completed solution."""
    violations: list[str] = []
    subjects = problem.get("subjects", {})
    teachers = problem.get("teachers", {})
    constraints = problem.get("constraints", {})
    requirements = {(row["class_name"], int(row["subject_id"])): row for row in problem.get("requirements", ())}
    assigned = {(slot["class_name"], slot["day"], int(slot["period_no"])): slot for slot in slots if not slot["is_free"]}
    for (class_name, day, period), slot in assigned.items():
        subject_id, teacher_id = slot["subject_id"], slot["teacher_id"]
        next_slot = assigned.get((class_name, day, period + 1))
        if next_slot and next_slot["subject_id"] == subject_id and not int(requirements.get((class_name, subject_id), {}).get("double_period_allowed", 0)):
            name = subjects.get(subject_id, {}).get("name", subject_id)
            violations.append(f"{class_name} has consecutive {name} periods on {day} ({period}-{period + 1}).")
        if constraints.get(teacher_id, {}).get(day, {}).get(period) == "PREFERRED_FREE":
            name = teachers.get(teacher_id, {}).get("name", teacher_id)
            violations.append(f"{name} teaches during a preferred-free slot on {day} period {period}.")
    for teacher_id, teacher in teachers.items():
        for day in problem.get("days", ()):
            periods = sorted(slot["period_no"] for slot in slots if slot["teacher_id"] == teacher_id and slot["day"] == day)
            run = 1
            for previous, current in zip(periods, periods[1:]):
                run = run + 1 if current == previous + 1 else 1
                if run == 5:
                    violations.append(f"{teacher.get('name', teacher_id)} has more than 4 consecutive periods on {day}.")
                    break
    for key, requirement in requirements.items():
        class_name, subject_id = key
        used_days = {slot["day"] for slot in slots if slot["class_name"] == class_name and slot["subject_id"] == subject_id}
        expected = min(int(requirement["periods_per_week"]), len(problem.get("days", ())))
        name = subjects.get(subject_id, {}).get("name", subject_id)
        if len(used_days) < max(1, expected - 1):
            violations.append(f"{class_name} {name} periods are concentrated on {len(used_days)} day(s).")
        lesson_days = [problem.get("days", ()).index(slot["day"]) for slot in slots if slot["class_name"] == class_name and slot["subject_id"] == subject_id]
        if len(lesson_days) >= 3 and sum(lesson_days) / len(lesson_days) < (len(problem.get("days", ())) - 1) / 3:
            violations.append(f"{class_name} {name} periods are front-loaded early in the week.")
    return violations
