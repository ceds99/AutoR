"""A derived, readable experiment ledger. run.json remains authoritative."""

from datetime import datetime, timezone
from pathlib import Path


def write_report(root, state):
    root = Path(root)
    config = state["config"]
    mode = "合成演示" if config["evaluator"]["kind"] == "demo" else "真实调用实验"
    lines = ["# AutoR 实验记录", "", "- 类型：" + mode,
             "- 开始时间：" + datetime.fromtimestamp(state["created_at"], timezone.utc).isoformat(),
             "- 目标：" + config["objective"], "- 策略：" + config["strategy"],
             "- 停止原因：" + str(state["stop_reason"]), "- 当前最佳：" + str(state["best"]),
             "- 调用计数：{} 次提案，{} 次评测".format(state["proposals_used"], state["evaluations_used"]),
             "- 累计调用时间：{:.2f} 秒".format(state["seconds_used"]),
             "- Outer 源码摘要：`" + state["outer_hash"] + "`", "",
             "这是搜索样本上的观测，不包含独立 held-out 评估、统计显著性或泛化改善结论。", "",
             "| 候选 | 父代 | 状态 | 均分 | 当次晋升 |", "|---|---|---|---|---|"]
    for c in state["candidates"]:
        lines.append("| {} | {} | {} | {} | {} |".format(c["id"], ", ".join(c["parents"]) or "seed",
                     c["status"], c.get("score", "—"), c.get("promoted", False)))
    if config["strategy"] in ("beam", "adaptive_beam"):
        from .search import search_view
        view = search_view(state)
        lines += ["", "## Beam 搜索", "",
                  "每代全部名额完成后才筛选；失败占用名额；淘汰只影响后续选父，历史最佳仍可导出。", "",
                  "- 当前 frontier：" + (", ".join(view["frontier"]) or "空"),
                  "- 搜索耗尽：" + str(view["exhausted"]), "",
                  "| 代 | 父代 | 已用/总名额 | 完成 | 保留 | 淘汰 | 失败 |",
                  "|---|---|---|---|---|---|---|"]
        for r in view["rounds"]:
            lines.append("| {} | {} | {}/{} | {} | {} | {} | {} |".format(
                r["generation"], ", ".join(r["parents"]), len(r["children"]), r["slots"], r["complete"],
                ", ".join(r["selected"]) or "—", ", ".join(r["pruned"]) or "—", ", ".join(r["failed"]) or "—"))
    for c in state["candidates"]:
        lines += ["", "## " + c["id"], "", "假设：" + c.get("hypothesis", "尚未生成")]
        if c.get("samples"):
            lines += ["", "独立测量：{}；复测完成：{}；样本均分：{}。".format(
                len(c["samples"]), c.get("confirmed", False),
                ", ".join(str(s["score"]) for s in c["samples"]))]
        if c.get("search_directive"):
            lines += ["", "候选方向与预算依据：" + str(c["search_directive"])]
        for label, key in (("诊断", "diagnosis"), ("失败原因", "error")):
            if c.get(key):
                lines += ["", label + "：" + str(c[key])]
        if c.get("changed_files"):
            lines += ["", "实际修改文件：" + ", ".join(c["changed_files"]),
                      "", "[修改 diff](candidates/" + c["id"] + "/change.patch)"]
        if "comparison" in c:
            x = c["comparison"]
            lines += ["", "相对父代：均分变化 {:+.4f}；改善 {}，退化 {}，不变 {}。".format(
                x["delta"], len(x["improved"]), len(x["regressed"]), len(x["unchanged"]))]
        if c.get("activation_verified"):
            lines += ["", "运行证据：每题回执与候选提示文件摘要一致；这证明输入被采用，不证明因果改善。"]
        if c.get("task_scores"):
            lines += ["", "| 任务 | 尝试 | 分数 |", "|---|---|---|"]
            for t in c["task_scores"]:
                lines.append("| {} | {} | {} |".format(t["task_id"], t.get("attempt", 0), t["score"]))
            lines += ["", "[评测证据](candidates/" + c["id"] + "/evaluation.json)"]
    path = root / "findings.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
