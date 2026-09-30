#!/usr/bin/env python3
"""Produce non-running configuration inventories and engineering closeout artifacts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex

from model import make_matrix


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_once(path, contents):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != contents:
        raise ValueError("Refusing to overwrite a different delivery artifact: " + str(path))
    if not path.exists():
        path.write_text(contents)


def inventories(batch):
    for name in ("sensitivity", "full"):
        value = dict(schema_version="steel-stack-v2-pending-configurations-v1", matrix=name,
                     execution_status="not-scheduled-not-executed", scientific_events=0,
                     events_per_task=None, blocks=None, campaign_seed=None, total_event_budget=None,
                     requires_explicit_allocation=True, configurations=make_matrix(name, [.5, 1.]))
        write_once(batch / "pending-scans" / f"{name}-configurations.json", json.dumps(value, indent=2) + "\n")


def visualization_macros(batch):
    tasks = json.loads((batch / "tasks.json").read_text())
    index = []
    for task in tasks:
        if task["stage"] != "smoke" or task["tile_thickness_mm"] not in (4, 24) or not task.get("accepted"):
            continue
        original = list((Path(task["run_dir"]) / "macros").glob("*.mac"))
        if len(original) != 1:
            raise ValueError("Missing unique accepted geometry macro")
        lines = []
        for line in original[0].read_text().splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens and (tokens[0] == "/run/beamOn" or tokens[0].startswith("/analysis/")):
                continue
            lines.append(line)
        lines += ["", "# Geometry inspection only: intentionally no /run/beamOn.",
                  "/vis/open TSGQtZB", "/vis/drawVolume", "/vis/viewer/set/style wireframe",
                  "/vis/viewer/set/viewpointThetaPhi 70 30 deg", "/vis/viewer/set/autoRefresh true",
                  "/vis/viewer/flush", ""]
        target = batch / "geometry-views" / (task["task_id"].removeprefix("smoke-") + ".mac")
        write_once(target, "\n".join(lines))
        index.append(dict(macro=str(target.relative_to(batch)), sha256=sha(target),
                          source_macro_sha256=sha(original[0]), configuration=task["config"],
                          scientific_events=0, viewer="TSGQtZB", run_manager="Serial",
                          visual_review_status="not-claimed"))
    write_once(batch / "geometry-views/index.json", json.dumps(index, indent=2) + "\n")
    return index


def closeout(batch):
    verification = json.loads((batch / "verification.json").read_text())
    if verification.get("all_passed") is not True:
        raise ValueError("Acceptance is incomplete; cannot issue a passed closeout")
    integration = json.loads((batch / "integration-checks.json").read_text())
    for key in ("unit_tests", "root_read", "analysis"):
        if integration.get(key, {}).get("passed") is not True:
            raise ValueError("Missing passing integration evidence: " + key)
    tasks = json.loads((batch / "tasks.json").read_text())
    if sum(t["events"] for t in tasks) != 130:
        raise ValueError("Engineering closeout only applies to the registered 130-event matrix")
    inventories(batch)
    views = visualization_macros(batch)
    if len(views) != 16:
        raise ValueError("Expected all sixteen endpoint geometry macros")
    exact = [t for t in tasks if t.get("compare_to")]
    candidate = json.loads((batch / "candidate/binary.json").read_text())
    reference = json.loads((batch / "reference/binary.json").read_text())
    description = "\n".join([
        "# 十层 Steel Module v2 工程验收报告", "",
        "状态：130 个计划内工程验收事件全部通过。此处不构成间隙敏感性研究或正式扫描结果。", "",
        "## 已完成", "",
        f"- {len(tasks)} 次运行、130 个事件；48 个候选几何全部完成实际 Geant4 检查。",
        f"- {len(exact)} 组精确比较覆盖旧行为回归、观察器 off/on 和固定种子重放。",
        "- 比较旧 ROOT 逐事件字段、直方图、summary 和 RNG 末态；不比较 ROOT 序列化元数据。",
        "- v2 账本核对全部出生、started、终态、sensor 合计和能量沉积分解；再闪烁保持开启。",
        "- ROOT 原生读取与 Python 分析均通过；工程 PNG/PDF 通过文件及渲染检查。",
        f"- {verification['protected_artifacts_unchanged']} 项原程序、默认环境或 latest 状态保持不变。",
        "- 生成 8 配置初筛清单和 48 配置完整候选清单，没有分配科学事件或提交 OSC。",
        f"- 生成 {len(views)} 份端点几何可视化宏；未把生成宏视为人工视觉确认。", "",
        "## 可追溯身份", "",
        f"- 参考程序 SHA-256：`{reference['sha256']}`。",
        f"- 候选程序 SHA-256：`{candidate['sha256']}`。",
        f"- Geant4 `{candidate['geant4_version']}`，`{candidate['architecture']}`，Serial。",
        "- 构建从实际冻结源码执行，包含未提交内容；输入、执行及审计分别保存校验值。", "",
        "## 证据与边界", "",
        "`verification.json` 是总体检查；`tasks.json` 指向每项输出、receipt 和审计。",
        "`reference/` 与 `candidate/` 保存源码和构建身份，`analysis-environment.json` 固定分析依赖。",
        "`analysis/` 的工程图表仅用于验证读取与分析链，不能用其排序布局或选择间隙。",
        "未来 OSC 使用对应架构重新构建的程序；本阶段仅离线验证适配器。", "",
        "## 保留的诊断记录", "",
        "Serial 下旧 ntuple 合并设置被忽略的 Analysis_W001 是明确识别的非物理提示。",
        "首个参考输出曾被初版日志检查误判，原记录保留，随后重审同一数据，未额外运行事件。",
        "所有真实失败、重试或重审均见 journal.jsonl 和各 attempt；不能用新的验收记录覆盖它们。", "",
        "最终间隙、最小有意义差异阈值和正式计算预算仍未确定。", ""
    ])
    write_once(batch / "ACCEPTANCE.zh.md", description)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=("inventories", "views", "closeout"))
    parser.add_argument("--batch-dir", required=True, type=Path)
    args = parser.parse_args()
    {"inventories": inventories, "views": visualization_macros, "closeout": closeout}[args.command](args.batch_dir.resolve())


if __name__ == "__main__":
    main()
