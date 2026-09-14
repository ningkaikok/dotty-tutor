"""运行 Tutor 离线契约检查：``uv run python -m evaluation.tutor.runner --check``。"""

from __future__ import annotations

import argparse
import json

from evaluation.tutor.cases import CASES, DIMENSIONS


def check_cases() -> dict[str, object]:
    ids = [case.case_id for case in CASES]
    dimensions = {case.dimension for case in CASES}
    if len(ids) != len(set(ids)):
        raise ValueError("评测 case_id 重复")
    if dimensions != set(DIMENSIONS):
        raise ValueError(f"评测维度不完整: {sorted(dimensions)}")
    counts = {dimension: sum(case.dimension == dimension for case in CASES) for dimension in DIMENSIONS}
    if any(count < 5 for count in counts.values()):
        raise ValueError(f"每个维度至少需要 5 个 case: {counts}")
    return {"cases": len(CASES), "dimensions": list(DIMENSIONS), "counts": counts, "status": "ok"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="检查确定性评测语料")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check_cases(), ensure_ascii=False, sort_keys=True))
        return
    parser.error("当前实验室只提供 --check；真实模型输出应通过统一指标函数接入")


if __name__ == "__main__":
    main()
