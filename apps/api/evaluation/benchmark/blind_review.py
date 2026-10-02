"""Prepare a model-blind human review packet and summarize completed ratings."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from evaluation.benchmark.contract import load_jsonl
from evaluation.benchmark.statistics import (
    paired_binary_difference,
    paired_bootstrap_ci,
)

SYSTEM_DIMENSION = "latency_cost"
PREFERENCES = {"A", "B", "tie", "neither"}


def prepare_packet(
    report: Mapping[str, Any],
    dataset: list[dict[str, Any]],
    *,
    report_sha256: str,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the dataset as the reference and hide both model labels and timing."""
    if report.get("status") != "completed":
        raise ValueError("paired run must be completed")
    baseline_model = report.get("baselineModel")
    candidate_model = report.get("candidateModel")
    if not all(isinstance(name, str) and name for name in (baseline_model, candidate_model)):
        raise ValueError("both model names are required")
    if baseline_model == candidate_model:
        raise ValueError("models must differ")
    eligible = [row for row in dataset if row.get("evaluationEligible") is True]
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(eligible):
        raise ValueError("paired result count does not match eligible cases")
    result_by_id = {row.get("caseId"): row for row in results if isinstance(row, dict)}
    if len(result_by_id) != len(results) or set(result_by_id) != {row["caseId"] for row in eligible}:
        raise ValueError("paired results must cover every eligible case exactly once")

    packet_id = hashlib.sha256(f"{report_sha256}:{seed}".encode()).hexdigest()[:20]
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    assignments: list[dict[str, str]] = []
    for row in eligible:
        case_id = row["caseId"]
        result = result_by_id[case_id]
        for field in ("taskDimension", "input", "expected", "rubric"):
            if result.get(field) != row.get(field):
                raise ValueError(f"{case_id}: {field} differs from reviewed dataset")
        if not all(isinstance(result.get(arm), dict) for arm in ("baseline", "candidate")):
            raise ValueError(f"{case_id}: both model arms are required")
        a_arm = "baseline" if rng.getrandbits(1) else "candidate"
        b_arm = "candidate" if a_arm == "baseline" else "baseline"
        cases.append({
            "caseId": case_id,
            "taskDimension": row["taskDimension"],
            "input": row["input"],
            "expected": row["expected"],
            "rubric": row["rubric"],
            "responseA": _review_response(result[a_arm]),
            "responseB": _review_response(result[b_arm]),
        })
        assignments.append({"caseId": case_id, "A": a_arm, "B": b_arm})
    packet = {
        "schemaVersion": 1,
        "packetId": packet_id,
        "datasetSha256": report.get("datasetSha256"),
        "caseCount": len(cases),
        "cases": cases,
    }
    key = {
        "schemaVersion": 1,
        "packetId": packet_id,
        "packetSha256": _packet_hash(packet),
        "reportSha256": report_sha256,
        "baselineModel": baseline_model,
        "candidateModel": candidate_model,
        "assignments": assignments,
    }
    return packet, key


def _packet_hash(packet: Mapping[str, Any]) -> str:
    payload = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _review_response(arm: Mapping[str, Any]) -> dict[str, Any]:
    status = arm.get("status")
    if status not in {"success", "failed"}:
        raise ValueError("model arm status must be success or failed")
    return {
        "status": status,
        "output": arm.get("output") if status == "success" else None,
        "errorType": arm.get("errorType") if status == "failed" else None,
    }


def summarize_ratings(
    packet: Mapping[str, Any], key: Mapping[str, Any], ratings: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail closed on incomplete ratings and never pool tutor with routing cases."""
    packet_id = packet.get("packetId")
    if not packet_id or packet_id != key.get("packetId") or packet_id != ratings.get("packetId"):
        raise ValueError("packet ID mismatch")
    if _packet_hash(packet) != key.get("packetSha256"):
        raise ValueError("blind packet content differs from assignment key")
    reviewer_id = ratings.get("reviewerId")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("reviewerId is required")
    reviewed_at = ratings.get("reviewedAt")
    if not isinstance(reviewed_at, str):
        raise ValueError("reviewedAt is required")
    try:
        parsed_reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("reviewedAt must be ISO-8601") from error
    if parsed_reviewed_at.tzinfo is None:
        raise ValueError("reviewedAt must include a timezone")
    cases = packet.get("cases")
    assignments = key.get("assignments")
    entries = ratings.get("ratings")
    if not isinstance(cases, list) or not isinstance(assignments, list) or not isinstance(entries, list):
        raise ValueError("cases, assignments and ratings must be arrays")
    if any(not isinstance(item, Mapping) for group in (cases, assignments, entries) for item in group):
        raise ValueError("case, assignment and rating entries must be objects")
    case_ids = [case.get("caseId") for case in cases]
    assignment_ids = [item.get("caseId") for item in assignments]
    rating_ids = [item.get("caseId") for item in entries]
    if len(set(case_ids)) != len(case_ids) or set(case_ids) != set(assignment_ids) or set(case_ids) != set(rating_ids):
        raise ValueError("ratings and assignment IDs must match packet cases exactly once")
    if len(cases) != len(assignments) or len(cases) != len(entries):
        raise ValueError("duplicate or missing case ratings")
    by_assignment = {item["caseId"]: item for item in assignments}
    by_rating = {item["caseId"]: item for item in entries}
    cohorts: dict[str, list[dict[str, Any]]] = {"tutorText": [], "systemRouting": []}
    for case in cases:
        case_id = case["caseId"]
        rating = by_rating[case_id]
        if not isinstance(rating.get("aPass"), bool) or not isinstance(rating.get("bPass"), bool):
            raise ValueError(f"{case_id}: aPass and bPass must be reviewed booleans")
        if rating.get("preferred") not in PREFERENCES:
            raise ValueError(f"{case_id}: preferred must be A, B, tie or neither")
        if not isinstance(rating.get("reason"), str) or not rating["reason"].strip():
            raise ValueError(f"{case_id}: a reason is required")
        assignment = by_assignment[case_id]
        if {assignment.get("A"), assignment.get("B")} != {"baseline", "candidate"}:
            raise ValueError(f"{case_id}: invalid arm assignment")
        baseline_pass = rating["aPass"] if assignment["A"] == "baseline" else rating["bPass"]
        candidate_pass = rating["aPass"] if assignment["A"] == "candidate" else rating["bPass"]
        preferred = rating["preferred"]
        if preferred in {"A", "B"}:
            preferred = assignment[preferred]
        cohort = "systemRouting" if case["taskDimension"] == SYSTEM_DIMENSION else "tutorText"
        cohorts[cohort].append({
            "caseId": case_id,
            "baselinePass": baseline_pass,
            "candidatePass": candidate_pass,
            "preferred": preferred,
            "reason": rating["reason"].strip(),
        })

    summaries: dict[str, Any] = {}
    for name, rows in cohorts.items():
        if not rows:
            continue
        baseline = [row["baselinePass"] for row in rows]
        candidate = [row["candidatePass"] for row in rows]
        summaries[name] = {
            "cases": len(rows),
            "pairedPassDifference": paired_binary_difference(baseline, candidate).as_dict(),
            "pairedBootstrap95": paired_bootstrap_ci(baseline, candidate).as_dict(),
            "preferenceCounts": {
                choice: sum(row["preferred"] == choice for row in rows)
                for choice in ("baseline", "candidate", "tie", "neither")
            },
        }
    return {
        "packetId": packet_id,
        "datasetSha256": packet.get("datasetSha256"),
        "reviewerId": reviewer_id.strip(),
        "reviewedAt": reviewed_at,
        "baselineModel": key.get("baselineModel"),
        "candidateModel": key.get("candidateModel"),
        "cohorts": summaries,
        "note": "Self-declared human review of synthetic cases; not a formal human gold corpus or general model ranking.",
    }


def render_html(packet: Mapping[str, Any]) -> str:
    """Embed only the blind packet in an offline form; keep the assignment key separate."""
    encoded = base64.b64encode(json.dumps(packet, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return _HTML.replace("__PACKET_B64__", encoded)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and summarize a blind human model review.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare")
    prepare.add_argument("--report", type=Path, required=True)
    prepare.add_argument("--dataset", type=Path, required=True)
    prepare.add_argument("--out-dir", type=Path, required=True)
    prepare.add_argument("--key-out", type=Path, required=True)
    prepare.add_argument("--seed", type=int)
    summarize = subcommands.add_parser("summarize")
    summarize.add_argument("--packet", type=Path, required=True)
    summarize.add_argument("--key", type=Path, required=True)
    summarize.add_argument("--ratings", type=Path, required=True)
    summarize.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            report_bytes = args.report.read_bytes()
            report = json.loads(report_bytes)
            if hashlib.sha256(args.dataset.read_bytes()).hexdigest() != report.get("datasetSha256"):
                raise ValueError("dataset SHA-256 differs from paired report")
            packet, key = prepare_packet(
                report, load_jsonl(args.dataset),
                report_sha256=hashlib.sha256(report_bytes).hexdigest(),
                seed=args.seed if args.seed is not None else secrets.randbits(64),
            )
            if args.key_out.resolve().is_relative_to(args.out_dir.resolve()):
                raise ValueError("assignment key must stay outside the shareable packet directory")
            args.out_dir.mkdir(parents=True, exist_ok=True)
            _write_json(args.out_dir / "blind-packet.json", packet)
            args.key_out.parent.mkdir(parents=True, exist_ok=True)
            _write_json(args.key_out, key)
            (args.out_dir / "blind-review.html").write_text(render_html(packet), encoding="utf-8")
            print(json.dumps({"packetId": packet["packetId"], "cases": packet["caseCount"], "outDir": str(args.out_dir)}))
        else:
            summary = summarize_ratings(
                json.loads(args.packet.read_text(encoding="utf-8")),
                json.loads(args.key.read_text(encoding="utf-8")),
                json.loads(args.ratings.read_text(encoding="utf-8")),
            )
            _write_json(args.out, summary)
            print(json.dumps({"ok": True, "out": str(args.out)}, ensure_ascii=False))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>陪练模型匿名配对审核</title>
<style>
body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px;color:#16302a;background:#f5f7f4}
h1,h2{line-height:1.3}header,.case{background:#fff;border:1px solid #d5dfd7;border-radius:12px;padding:20px;margin:16px 0}
.meta{color:#4d6559}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}.arm{background:#f0f5f1;padding:12px;border-radius:8px}
pre{white-space:pre-wrap;word-break:break-word;font:14px/1.6 ui-monospace,monospace}label{display:block;margin:10px 0}
select,textarea,input{font:inherit}textarea{width:100%;min-height:70px;box-sizing:border-box}button{padding:8px 14px;margin:5px;cursor:pointer}
@media(max-width:700px){.pair{grid-template-columns:1fr}}
</style></head><body>
<header><h1>陪练模型匿名配对审核</h1>
<p>逐题查看输入、参考答案、审核要点与回答 A/B。按 rubric 判定每个回答是否通过，再选更好的回答并写明原因。A/B 每题重新随机分配；本页不包含模型名称。这里的材料是合成评测案例，不是正式人工金标准。</p>
<label>审核者标识 <input id="reviewer" autocomplete="name" placeholder="填写你的标识"></label>
<p id="progress"></p><button id="export" type="button">导出审核 JSON</button>
<label>导入此前审核 JSON <input id="import" type="file" accept="application/json,.json"></label>
</header><main id="cases"></main>
<script>
const packet=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("__PACKET_B64__"),c=>c.charCodeAt(0))));
const storageKey="dotty-blind-review-"+packet.packetId;
let state={packetId:packet.packetId,reviewerId:"",ratings:{}};
try{const saved=JSON.parse(localStorage.getItem(storageKey));if(saved?.packetId===packet.packetId)state=saved}catch{}
const reviewer=document.getElementById("reviewer"),root=document.getElementById("cases"),progress=document.getElementById("progress");
reviewer.value=state.reviewerId||"";
function save(){state.reviewerId=reviewer.value.trim();try{localStorage.setItem(storageKey,JSON.stringify(state))}catch{}updateProgress()}
reviewer.addEventListener("input",save);
function text(tag,value,parent){const el=document.createElement(tag);el.textContent=value;parent.append(el);return el}
function field(label,value,parent){const wrap=document.createElement("div");text("strong",label,wrap);text("pre",JSON.stringify(value,null,2),wrap);parent.append(wrap)}
function select(label,values,current,onChange,parent){const wrap=document.createElement("label");text("span",label+" ",wrap);const el=document.createElement("select");
for(const [value,title] of values){const option=document.createElement("option");option.value=value;option.textContent=title;el.append(option)}
el.value=current??"";el.addEventListener("change",()=>onChange(el.value));wrap.append(el);parent.append(wrap)}
function draw(){root.replaceChildren();for(const [i,c] of packet.cases.entries()){
const card=document.createElement("section");card.className="case";text("h2",`${i+1}/${packet.caseCount} ${c.caseId}`,card);
text("p",`维度：${c.taskDimension}${c.taskDimension==="latency_cost"?"（系统路由任务，单独统计）":""}`,card).className="meta";
field("任务输入",c.input,card);field("参考期望",c.expected,card);field("审核要点",c.rubric,card);
const pair=document.createElement("div");pair.className="pair";card.append(pair);
for(const side of ["A","B"]){const arm=document.createElement("div");arm.className="arm";pair.append(arm);text("h3","回答 "+side,arm);
field("模型输出",c["response"+side],arm)}
const rating=state.ratings[c.caseId]??={aPass:null,bPass:null,preferred:"",reason:""};
for(const side of ["A","B"]){const key=side.toLowerCase()+"Pass";
select("回答 "+side+" 是否满足 rubric",[["","待判定"],["true","通过"],["false","不通过"]],rating[key]===null?"":String(rating[key]),
v=>{rating[key]=v===""?null:v==="true";save()},card)}
select("综合偏好",[["","待判定"],["A","A"],["B","B"],["tie","同等"],["neither","均不合格"]],rating.preferred,
v=>{rating.preferred=v;save()},card);
const label=document.createElement("label");text("span","判定理由（必填）",label);const notes=document.createElement("textarea");notes.value=rating.reason||"";
notes.addEventListener("input",()=>{rating.reason=notes.value;save()});label.append(notes);card.append(label);root.append(card)}updateProgress()}
function updateProgress(){const done=packet.cases.filter(c=>{const r=state.ratings[c.caseId];return r&&typeof r.aPass==="boolean"&&typeof r.bPass==="boolean"&&r.preferred&&r.reason?.trim()}).length;
progress.textContent=`已完成 ${done}/${packet.caseCount} 题。可以随时导出或导入继续审核。`}
document.getElementById("export").addEventListener("click",()=>{save();const ratings=packet.cases.map(c=>({caseId:c.caseId,...(state.ratings[c.caseId]||{aPass:null,bPass:null,preferred:"",reason:""})}));
const data={packetId:packet.packetId,reviewerId:state.reviewerId,reviewedAt:new Date().toISOString(),ratings};
const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="blind-ratings-"+packet.packetId+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
document.getElementById("import").addEventListener("change",async e=>{const file=e.target.files?.[0];if(!file)return;
try{const data=JSON.parse(await file.text());if(data.packetId!==packet.packetId||!Array.isArray(data.ratings))throw Error("审核包 ID 不匹配");
state={packetId:packet.packetId,reviewerId:data.reviewerId||"",ratings:Object.fromEntries(data.ratings.map(r=>[r.caseId,r]))};reviewer.value=state.reviewerId;save();draw()}
catch(error){alert("导入失败："+error.message)}});
draw();
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
