"""Evidence-driven beam scheduling; all decisions rebuild from the ledger."""

from .search import TERMINAL


def vector(candidate):
    grouped = {}
    for task in candidate.get("task_scores", []):
        grouped.setdefault(task["task_id"], []).append(task["score"])
    return {key: sum(values) / len(values) for key, values in grouped.items()}


def bounds(candidate):
    scores = [s["score"] for s in candidate.get("samples", [])]
    return (min(scores), max(scores)) if scores else (candidate["score"], candidate["score"])


def improved(candidate, reference):
    # Replicated separation, not a significance test or a confidence interval.
    return candidate.get("confirmed", False) and bounds(candidate)[0] > bounds(reference)[1]


def complementary(candidate, peers):
    values = vector(candidate)
    return [key for key, value in values.items()
            if value > max((vector(p).get(key, value) for p in peers), default=value)]


def diverse_select(candidates, width):
    remaining = sorted(candidates, key=lambda c: (-c["score"], c["id"]))
    selected = remaining[:1]
    remaining = remaining[1:]
    while remaining and len(selected) < width:
        # Reward additional task capability, rather than different prompt wording.
        def rank(c):
            gains = [max(0, v - max(vector(p).get(k, v) for p in selected))
                     for k, v in vector(c).items()]
            return (-sum(gains), -c["score"], c["id"])
        chosen = min(remaining, key=rank)
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


def confirmation_reason(state, candidate):
    required = state["config"].get("search", {}).get("confirmation_samples", 2)
    if len(candidate.get("samples", [])) >= required:
        return None
    if not candidate["parents"]:
        return "confirm_seed_reference"
    # A persistent reason prevents a later sample or restart changing the plan.
    if candidate.get("confirmation_reason"):
        return candidate["confirmation_reason"]
    seed = state["candidates"][0]
    if candidate["score"] > bounds(seed)[1]:
        return "confirm_baseline_improvement"
    peers = [c for c in state["candidates"] if c["id"] != candidate["id"]
             and c["status"] == "evaluated"]
    if complementary(candidate, peers):
        return "confirm_complementary_capability"
    return None


def adaptive_view(state):
    settings = state["config"].get("search", {})
    width = settings.get("beam_width", 2)
    maximum = settings.get("children_per_parent", 2)
    candidates = state["candidates"]
    seed = candidates[0]
    view = {"beam_width": width, "children_per_parent": maximum, "rounds": [],
            "frontier": [], "next_parent": None, "next_parents": [],
            "directive": None, "exhausted": False}
    if seed["status"] != "evaluated":
        return view
    frontier = [seed]
    while frontier:
        ids = [c["id"] for c in frontier]
        allocations = {}
        reasons = {}
        for parent in frontier:
            if parent is seed:
                reason = "initial_exploration"
            elif improved(parent, seed):
                reason = "replicated_improvement"
            elif complementary(parent, [p for p in frontier if p is not parent]):
                reason = "complementary_capability"
            else:
                reason = "limited_non_improving_exploration"
            allocations[parent["id"]] = 1 if reason == "limited_non_improving_exploration" else maximum
            reasons[parent["id"]] = reason
        # A mixed child consumes ONE slot, attributed to its primary parent.
        children = [c for c in candidates if c["parents"] and c["parents"][0] in ids]
        counts = {p: sum(c["parents"][0] == p for c in children) for p in ids}
        pending = any(c["status"] not in TERMINAL for c in children)
        complete = all(counts[p] >= allocations[p] for p in ids) and not pending
        measured = [c for c in children if c["status"] == "evaluated"]
        selected = diverse_select(measured, width) if complete else []
        view["frontier"] = ids
        eligible = sorted((c for c in frontier if c is not seed and improved(c, seed)),
                          key=lambda c: (-c["score"], c["id"]))
        mix_pair = eligible[:2] if settings.get("mixing", False) and len(eligible) >= 2 else []
        mix_used = any(len(c["parents"]) == 2 for c in children)
        if children:
            view["rounds"].append({
                "generation": frontier[0]["depth"] + 1, "parents": ids,
                "children": [c["id"] for c in children], "slots": sum(allocations.values()),
                "allocations": allocations, "allocation_reasons": reasons, "complete": complete,
                "selected": [c["id"] for c in selected],
                "pruned": [c["id"] for c in measured if c not in selected] if complete else [],
                "failed": [c["id"] for c in children if c["status"] in TERMINAL - {"evaluated"}],
                "mix_eligible": [c["id"] for c in mix_pair], "mix_used": mix_used,
            })
        if not complete:
            available = [c for c in frontier if counts[c["id"]] < allocations[c["id"]]]
            if not pending and available:
                parent = min(available, key=lambda c: counts[c["id"]])
                parents = [parent]
                if mix_pair and not mix_used and mix_pair[0] in available:
                    parents = mix_pair
                    parent = parents[0]
                operator = "mix" if len(parents) == 2 else (
                    "failure_analysis", "verification", "simplify")[
                        (parent["depth"] + counts[parent["id"]]) % 3]
                values = vector(parent)
                weakest = [k for k, value in values.items() if value == min(values.values())]
                view.update(next_parent=parent["id"], next_parents=[p["id"] for p in parents],
                            directive={"operator": operator, "focus_tasks": weakest,
                                       "allocation_reason": reasons[parent["id"]],
                                       "remaining_parent_slots": allocations[parent["id"]] - counts[parent["id"]]})
            return view
        frontier = selected
    view.update(frontier=[], exhausted=True)
    return view
