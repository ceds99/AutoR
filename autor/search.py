"""Deterministic search policies derived from the candidate ledger.

No second authoritative queue: rebuilding from the ledger preserves partial rounds
across restarts. Failed attempts occupy child slots just like measured children.
"""

from .storage import AutorError


TERMINAL = {"evaluated", "rejected", "error", "interrupted"}


def beam_view(state):
    settings = state["config"].get("search", {})
    width = settings.get("beam_width", 2)
    per_parent = settings.get("children_per_parent", 2)
    candidates = state["candidates"]
    seed = candidates[0]
    view = {"beam_width": width, "children_per_parent": per_parent,
            "rounds": [], "frontier": [], "next_parent": None, "exhausted": False}
    if seed["status"] != "evaluated":
        return view
    frontier = [seed]
    while frontier:
        ids = [c["id"] for c in frontier]
        children = [c for c in candidates if any(p in ids for p in c["parents"])]
        counts = {p: sum(p in c["parents"] for c in children) for p in ids}
        pending = any(c["status"] not in TERMINAL for c in children)
        complete = all(n >= per_parent for n in counts.values()) and not pending
        measured = sorted((c for c in children if c["status"] == "evaluated"),
                          key=lambda c: (-c["score"], c["id"]))
        selected = measured[:width] if complete else []
        view["frontier"] = ids
        # Do not add a fictitious empty round after a fully completed generation.
        if children:
            view["rounds"].append({
                "generation": frontier[0]["depth"] + 1, "parents": ids,
                "children": [c["id"] for c in children],
                "slots": len(ids) * per_parent, "complete": complete,
                "selected": [c["id"] for c in selected],
                "pruned": [c["id"] for c in measured[width:]] if complete else [],
                "failed": [c["id"] for c in children if c["status"] in TERMINAL - {"evaluated"}],
            })
        if not complete:
            available = [c for c in frontier if counts[c["id"]] < per_parent]
            if not pending and available:
                # Round-robin, with the previous generation's rank as tie-breaker.
                view["next_parent"] = min(available, key=lambda c: counts[c["id"]])["id"]
            return view
        frontier = selected
    view.update(frontier=[], exhausted=True)
    return view


def select_parent(state):
    items = [c for c in state["candidates"] if c["status"] == "evaluated"]
    if not items:
        raise AutorError("No measured parent is available")
    strategy = state["config"]["strategy"]
    if strategy in ("beam", "adaptive_beam"):
        view = search_view(state)
        if view["exhausted"]:
            return None
        if view["next_parent"] is None:
            raise AutorError("Beam round still has an unfinished candidate")
        return next(c for c in items if c["id"] == view["next_parent"])
    if strategy == "hill_climb":
        return next(c for c in items if c["id"] == state["best"])
    # Preserve the v0.1 breadth-first policy, including its fallback.
    counts = {c["id"]: sum(c["id"] in x["parents"] for x in state["candidates"]) for c in items}
    available = [c for c in items if counts[c["id"]] < 2] or items
    return min(available, key=lambda c: (c["depth"], counts[c["id"]], -c["score"], c["id"]))


def search_view(state):
    if state["config"]["strategy"] == "adaptive_beam":
        from .adaptive import adaptive_view
        return adaptive_view(state)
    return beam_view(state)
