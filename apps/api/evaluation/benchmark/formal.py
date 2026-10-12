"""Formal offline comparison: admitted human gold, complete arms and two verified blind raters."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any, Mapping

from evaluation.benchmark.blind_review import (
    prepare_packet,
    render_html,
    summarize_ratings,
)
from evaluation.benchmark.contract import load_jsonl, validate_records


def prepare_formal(report: dict[str, Any], dataset: list[dict[str, Any]], identities: Mapping[str, Any], *, dataset_sha256: str, report_sha256: str, seed: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reject development probes before producing any formal review packet."""
    validation = validate_records(dataset, trusted_identities=identities)
    if not validation.ok:
        raise ValueError("人工金标准门槛未满足：" + "; ".join(validation.problems))
    if report.get("datasetSha256") != dataset_sha256:
        raise ValueError("模型运行不属于这份固定金标准文件")
    admitted = [{**row, "input": row.get("redactedInput", row.get("input")), "evaluationEligible": True}
                for row in dataset if row.get("counted") is True and row.get("sourceKind") == "human"]
    private_seed = secrets.randbits(64) if seed is None else seed
    packet, key = prepare_packet(report, admitted, report_sha256=report_sha256, seed=private_seed)
    for result in report["results"]:
        for name in ("baseline", "candidate"):
            arm = result[name]
            if arm["status"] == "success" and not arm.get("output"):
                raise ValueError("成功的模型结果缺少输出")
            if arm["status"] == "failed" and not arm.get("errorType"):
                raise ValueError("失败的模型结果缺少错误类型")
    key["formalGoldValidation"] = validation.as_dict()
    key["formalProtocol"] = "human-gold-double-blind-v1"
    key["privateSeed"] = private_seed
    return packet, key


def summarize_formal(packet: dict[str, Any], key: dict[str, Any], ratings: list[dict[str, Any]], identities: Mapping[str, Any]) -> dict[str, Any]:
    """Keep disagreements visible; paired statistics never silently become a ranking."""
    if key.get("formalProtocol") != "human-gold-double-blind-v1" or key.get("formalGoldValidation", {}).get("ok") is not True:
        raise ValueError("缺少已验证的正式金标准协议")
    if len(ratings) != 2:
        raise ValueError("正式评测需要两名独立盲审者")
    people = set()
    summaries = []
    for rating in ratings:
        actor = str(rating.get("reviewerId", ""))
        identity = identities.get(actor, {})
        if (identity.get("identityVerified") is not True or identity.get("actorKind") != "individual"
                or not identity.get("personKey") or not identity.get("evidenceRef")):
            raise ValueError("盲审者身份未经独立核验")
        person = identity["personKey"]
        if person in people:
            raise ValueError("盲审者必须是不同自然人")
        people.add(person)
        summary = summarize_ratings(packet, key, rating)
        summary["note"] = "Independently verified blind review of this fixed human-gold dataset; no general production ranking."
        by_id = {entry["caseId"]: entry for entry in rating["ratings"]}
        for case in packet["cases"]:
            for label in ("A", "B"):
                if case[f"response{label}"]["status"] == "failed" and by_id[case["caseId"]][label.lower() + "Pass"]:
                    raise ValueError("失败的模型臂不能被标记为通过")
        summaries.append(summary)
    first = {item["caseId"]: item for item in ratings[0]["ratings"]}
    second = {item["caseId"]: item for item in ratings[1]["ratings"]}
    disagreements = [case_id for case_id in first if any(first[case_id][field] != second[case_id][field] for field in ("aPass", "bPass", "preferred"))]
    return {"protocol": key["formalProtocol"], "datasetSha256": packet["datasetSha256"],
            "caseCount": packet["caseCount"], "reviewerSummaries": summaries,
            "unresolvedCaseIds": disagreements, "formalRankingAvailable": not disagreements,
            "status": "requires_human_adjudication" if disagreements else "reviewed_comparison",
            "interpretation": "分层配对差值与区间仅适用于这份固定数据；不自动宣称生产模型排名"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "summarize"])
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, nargs=2)
    parser.add_argument("--html", type=Path, help="生成离线盲审表单；密钥必须保存在审核目录外")
    args = parser.parse_args()
    identities = json.loads(args.identities.read_text())
    if args.out.exists():
        parser.error("输出已存在，不覆盖审核证据")
    try:
        if args.mode == "prepare":
            if not args.dataset or not args.report or args.key.exists():
                raise ValueError("需要 dataset/report 及新的独立密钥文件")
            packet, key = prepare_formal(json.loads(args.report.read_text()), load_jsonl(args.dataset), identities,
                dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(), report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest())
            args.key.parent.mkdir(parents=True, exist_ok=True)
            args.key.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n")
            output = packet
        else:
            if not args.packet or not args.ratings or not args.dataset or not args.report:
                raise ValueError("需要 packet、两份 ratings、原 dataset/report 以重新验证门槛")
            packet = json.loads(args.packet.read_text())
            key = json.loads(args.key.read_text())
            expected_packet, expected_key = prepare_formal(json.loads(args.report.read_text()), load_jsonl(args.dataset), identities,
                dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(), report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(), seed=key.get("privateSeed"))
            if packet != expected_packet or key != expected_key:
                raise ValueError("审核包或密钥与原始运行、金标准不匹配")
            output = summarize_formal(packet, key,
                [json.loads(path.read_text()) for path in args.ratings], identities)
        if args.mode == "prepare" and args.html:
            if args.html.exists() or args.key.parent.resolve() == args.html.parent.resolve():
                raise ValueError("表单不能覆盖已有文件，密钥须保存在审核目录外")
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(render_html(output).replace("合成案例", "固定人工金标准案例"))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "formalRankingAvailable": False, "problem": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
