"""Archive this conversation's supplied scoreboard and immutable candidate snapshots."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
DEST = Path(__file__).resolve().parent


def save(name, value):
    (DEST / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive_candidate(name, folder, official=None):
    source = ROOT / folder
    target = DEST / "artifacts" / name
    target.mkdir(parents=True, exist_ok=True)
    report = json.loads((source / "validation_report.json").read_text(encoding="utf-8-sig"))
    files = []
    for filename in ("初赛结果_棒材优化.json", "初赛结果_棒材优化.zip", "validation_report.json", "comparison.json", "official_feedback.json", "提交说明.md"):
        src = source / filename
        if not src.exists():
            continue
        dst = target / filename
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            raise RuntimeError(f"Refusing to replace a different archived snapshot: {dst}")
        shutil.copy2(src, dst)
        digest = sha256(dst.read_bytes()).hexdigest()
        if filename.endswith(".zip"):
            assert digest == report["zip_sha256"]
        elif filename == "初赛结果_棒材优化.json":
            assert digest == report["json_sha256"]
        files.append({"original_path": src.relative_to(ROOT).as_posix(), "archive_path": dst.relative_to(DEST).as_posix(), "sha256": digest, "bytes": dst.stat().st_size})
    return {
        "id": name,
        "source_folder": folder,
        "json_sha256": report["json_sha256"],
        "zip_sha256": report["zip_sha256"],
        "official_feedback": official,
        "official_status": "user_reported_feasible" if official else "no_official_feedback_received",
        "local_metrics_after_packaging": report["after"],
        "local_independent_check": report["independent_platform_check"],
        "production": report.get("production"),
        "files": files,
        "association_provenance": "Conversation identifies the package returned immediately before the user's official feedback; hashes recomputed from local files. No server-side submission receipt available." if official else "Latest package provided in conversation; no subsequent user-reported official evaluation for this hash as of archival.",
    }


def main():
    rows = [
        (None,"AIC-2026-37219102","6","2026-08-28 08:50:47","100.0000","2026-08-28 08:51:04"),
        (None,"AIC-2026-85100494","吴彦组","2026-09-14 16:58:15","100.0000","2026-09-14 16:58:30"),
        (None,"AIC-2026-67472960","我是奶龙","2026-08-25 22:53:59","100.0000","2026-08-25 22:54:28"),
        (4,"AIC-2026-78874705","可以打一辈子竞赛吗？我什么都不会做的！","2026-08-25 11:47:25","100.0000","2026-08-25 11:47:42"),
        (5,"AIC-2026-62805765","gyfddd","2026-08-24 19:49:17","100.0000","2026-08-24 19:49:20"),
        (6,"AIC-2026-61769633","帝皇侠队","2026-09-01 14:57:27","100.0000","2026-09-01 14:57:52"),
        (7,"AIC-2026-82755648","PUBG","2026-08-16 20:01:04","99.5600","2026-08-16 20:01:17"),
        (8,"AIC-2026-87269334","冶金机械队","2026-09-15 19:01:56","99.4500","2026-09-15 19:02:30"),
        (9,"AIC-2026-35367646","钢好有解","2026-09-12 21:27:59","99.4500","2026-09-12 21:28:30"),
        (10,"AIC-2026-98653267","姜王博士","2026-08-24 11:15:43","99.2800","2026-08-24 11:15:49"),
        (11,"AIC-2026-31726349","鲤鱼小分队","2026-09-13 23:46:42","99.0600","2026-09-13 23:47:00"),
        (12,"AIC-2026-78445472","南北绿豆","2026-09-15 11:23:10","99.0500","2026-09-15 11:23:27"),
        (13,"AIC-2026-13208788","钢鉴无漏","2026-09-16 01:24:46","99.0200","2026-09-16 01:25:10"),
        (14,"AIC-2026-89721358","特种兵小队","2026-09-06 13:53:42","98.8600","2026-09-06 13:54:00"),
        (15,"AIC-2026-77739543","真的学不会","2026-09-16 00:09:03","98.7800","2026-09-16 00:09:17"),
        (16,"AIC-2026-69548917","武昌鱼队","2026-09-10 22:13:21","98.7100","2026-09-10 22:13:46"),
        (17,"AIC-2026-74985881","淡淡顺顺队","2026-09-15 19:30:09","98.6500","2026-09-15 19:30:26"),
        (18,"AIC-2026-48851622","大狗叫","2026-08-23 19:33:25","98.4500","2026-08-23 19:33:30"),
        (19,"AIC-2026-46706374","我才是校花","2026-09-15 00:15:41","98.4300","2026-09-15 00:16:08"),
        (20,"AIC-2026-18465797","这次能行","2026-08-31 17:31:34","98.3500","2026-08-31 17:31:37"),
    ]
    keys = ("rank", "entry_id", "team", "submitted_at_display", "score_display", "evaluated_at_display")
    board = {
        "source": "User pasted a 20-row leaderboard table in this conversation; no independent retrieval from platform",
        "archival_date_local": "2026-09-16",
        "timestamp_timezone": "not specified in pasted table; retained exactly as displayed",
        "first_three_ranks": "null because source rank cells were blank; do not infer 1/2/3",
        "rows": [dict(zip(keys, row)) for row in rows],
        "facts": {"row_count": len(rows), "displayed_100_count": sum(row[4] == "100.0000" for row in rows), "displayed_at_least_99_count": sum(float(row[4]) >= 99 for row in rows)},
        "limitations": "This excerpt does not establish score-component capping, evaluator formulas, the methods used by other teams, or the fraction of all teams reaching 99.",
    }
    save("leaderboard_top20.json", board)
    table = "| 排名（原文） | 参赛编号 | 团队名称 | 提交时间（原文） | 分数（原文） | 打分时间（原文） |\n| --- | --- | --- | --- | --- | --- |\n"
    table += "\n".join("| " + " | ".join("" if x is None else str(x) for x in row) + " |" for row in rows) + "\n"
    (DEST / "leaderboard_top20.md").write_text("# 用户提供的榜单快照\n\n保存日期：2026-09-16。来源：本会话用户粘贴表格；没有重新查询平台。前三行排名原文为空，以下保留为空。时间按原文保存，未推断时区。\n\n" + table + "\n事实：该20行摘录中，6行显示100.0000，13行显示至少99分。不能据此确定评分封顶方式，也不能计算全部参赛队伍中达到99分的比例。\n", encoding="utf-8")
    feedback87 = {
        "source": "User-provided platform result text and leaderboard screenshot in this conversation",
        "raw_result_text": "可行; 锯切刀数=113683, 成材率=86.63%, 覆盖率=99.98%, 子分(刀/材/覆/时)=79.17/86.63/99.98/100.0",
        "feasible": True, "total_score_display": "87.6500", "rank_display": 34,
        "submitted_at_display": "2026-09-15 22:38:08", "evaluated_at_display": "2026-09-15 22:38:31",
        "knives": 113683, "yield_percent": 86.63, "coverage_percent": 99.98,
        "subscores": {"knives": 79.17, "material": 86.63, "coverage": 99.98, "time": 100.0},
    }
    feedback96 = {
        "source": "User-provided platform result text and leaderboard screenshot in this conversation",
        "raw_result_text": "可行; 锯切刀数=92913, 成材率=92.51%, 覆盖率=99.98%, 子分(刀/材/覆/时)=96.86/92.51/99.98/100.0",
        "feasible": True, "total_score_display": "96.4900", "rank_display": 28,
        "submitted_at_display": "2026-09-16 08:32:52", "evaluated_at_display": "2026-09-16 08:33:23",
        "knives": 92913, "yield_percent": 92.51, "coverage_percent": 99.98,
        "subscores": {"knives": 96.86, "material": 92.51, "coverage": 99.98, "time": 100.0},
    }
    candidates = [archive_candidate("official_87_65", "submission_fixed", feedback87), archive_candidate("official_96_49", "submission_optimized", feedback96), archive_candidate("local_97_868663", "submission_continued")]
    save("submission_evidence.json", {"archived_at_utc": datetime.now(timezone.utc).isoformat(), "candidate_records": candidates})
    phase = {
        "user_instruction_raw": "ok,先将这组数据给存储起来,然后先达到正常能到的上限99,再检查有没有hack的办法可到100",
        "sequence": [
            {"phase": 1, "name": "Archive provided evidence", "status": "complete", "artifacts": ["leaderboard_top20.json", "leaderboard_top20.md", "submission_evidence.json", "artifacts/"]},
            {"phase": 2, "name": "Optimize valid physical cutting plans toward official score >=99", "status": "pending_at_archive_time", "success_requires": "Official feedback for the exact submitted JSON hash; local estimate alone is not achievement"},
            {"phase": 3, "name": "Inspect scoring boundaries / potential route to 100", "status": "deferred_until_phase_2", "scope_note": "The user's later request remains pending; scoreboard does not establish any evaluator weakness."},
        ],
        "current_verified_official_best": {"score": 96.49, "json_sha256": candidates[1]["json_sha256"], "verification_source": "user-reported platform feedback"},
        "latest_unverified_local_candidate": {"estimated_score": 97.868663152787, "knives": 92043, "yield_rate": 0.9586836617016302, "json_sha256": candidates[2]["json_sha256"]},
        "hypotheses_not_confirmed": [
            "Knife score baseline is 90000, inferred from feedback.",
            "Current local score assumes weights 40/30/20/10, individual knife component capped at100, and time component100; not evaluator equivalence.",
            "Alternative: knife component is not capped, but final total is capped at100. Leaderboard alone does not prove this.",
            "148/150 material upper bound and total99.6 follow only under the stated local physical and capped-score assumptions; neither is an established official bound.",
            "Maximum100 rounds and per-order5% surplus are algorithm choices, not verified official upper constraints.",
        ],
        "known_limits": ["4999 valid input orders; invalid source order A20260949 has -1100mm size and remains excluded, not silently repaired.", "Local and official knife/material metrics differ; do not treat local>=99 as official>=99.", "At archival time there is no official feedback for the92043-knife candidate."],
    }
    save("stage_status.json", phase)
    lines = ["# 2026-09-16 证据与阶段状态", "", "用户要求：先存储数据，再把正常可行方案优化到99分，之后再检查评分边界是否存在达到100分的办法。这里记录存档当时的状态；99是目标，不是已证明的正常算法上界。", "", "| 版本 | 官方总分 | 官方刀数 | 官方成材率 | 本地刀数 | 本地成材率 | 本地估分 |", "| --- | --- | --- | --- | --- | --- | --- |", "| 首个可行包 | 87.65 | 113683 | 86.63% | 116330 | 87.58197% | 未记为官方等价估分 |", "| 已提交优化包 | 96.49 | 92913 | 92.51% | 95268 | 93.52429% | 95.84142 |", "| 最新继续搜索包 | 尚无反馈 | — | — | 92043 | 95.86837% | 97.868663 |", "", "上述官方结果来自用户转述与截图，未直接查询服务器。提交文件关联依据是会话中的包与后续反馈；保存实际文件哈希，没有服务器提交回执。最新包只有本地校验通过，不能标为官方可行或官方97.87分。", "", "## 文件与校验", "", "- [完整20行榜单JSON](leaderboard_top20.json) / [阅读版](leaderboard_top20.md)。前三行排名为空；6行显示100，13行至少99。这不是全部参赛队伍的统计。", "- [提交证据JSON](submission_evidence.json)：反馈原文、详细本地指标、三份提交JSON/ZIP的SHA256与源路径。", "- [阶段状态JSON](stage_status.json)：已存档→正常可行优化至官方99→后续评分边界分析。", "- `artifacts/` 保存三份JSON、ZIP和原始校验报告快照；原报告的接受状态按生成时原样保留，后来的用户官方反馈见证据JSON。", "", "| 提交JSON快照 | SHA256 |", "| --- | --- |"]
    for c in candidates:
        lines.append(f"| [方案快照 {c['id']}](artifacts/{c['id']}/初赛结果_棒材优化.json) | `{c['json_sha256']}` |")
    lines += ["", "## 必须区分的假设", "", "90000刀基准、刀数子分是否封顶、总分是否封顶、完整官方物料计量均未完全确认。目前只有两个官方样本，且刀数子分都低于100，无法区分刀数子分封顶与不封顶。六队满分是事实，不是评分漏洞的证据。", "", "在每轮物理长度最多150m、共损耗2m，并且各子分封顶、权重40/30/20/10等假设同时成立时，可推出材料率上限148/150、总分上限99.6；这是条件模型推论，不是官方上界。", "", "5%超产上限与100轮上限来自当前算法设置，不是已核实的官方边界。最新方案实际总增产1.832374%（相对向上取整的整支需求），单订单最大5%。4999个有效订单之外的负定尺订单A20260949仍单独排除。", "", "阶段2只有收到对应文件哈希的官方>=99反馈才算达到；在此之前保持阶段3待办。"]
    (DEST / "EVIDENCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert len(board["rows"]) == 20
    assert all(board["rows"][i]["rank"] is None for i in range(3))
    assert board["facts"] == {"row_count":20, "displayed_100_count":6, "displayed_at_least_99_count":13}
    print(json.dumps({"directory": str(DEST), "leaderboard_rows":20, "displayed_100":6, "candidates": [{"id":c["id"], "json_sha256":c["json_sha256"]} for c in candidates]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
