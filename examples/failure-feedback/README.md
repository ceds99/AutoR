# 失败反馈验收 seed

`prompt.md` 故意保留了 scaffold-only 配置：要求实现函数签名但返回 None。这个人为 harness 缺陷用于取得真实测试失败，让 outer 根据反馈提出通用修改。

不要把此案例的分数变化当作自然任务上的能力增益，也不要改动评分器来制造失败。使用固定 HumanEval/0 原始测试，只跑 baseline 和一个子代。运行命令、预算及凭证说明见 [运行手册](../../docs/runbook.md)。
