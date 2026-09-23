"""Append the user's 2026-09-16 10:02 platform feedback without altering packages."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PACKAGE = ROOT / "submission_normal_985"
ARCHIVE = HERE / "artifacts" / "official_89_19"
EXPECTED_JSON_SHA256 = "5f53886795cdffd0a783f821fb306c2076031d03bf9029d720cabb7e8c4af72c"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source_json = PACKAGE / "初赛结果_棒材优化.json"
    source_zip = PACKAGE / "初赛结果_棒材优化.zip"
    assert sha(source_json) == EXPECTED_JSON_SHA256, "Package changed; do not silently associate feedback"
    assert not ARCHIVE.exists(), "Archive already exists; do not overwrite historical evidence"
    ARCHIVE.mkdir(parents=True)
    for name in ("EVIDENCE.md", "stage_status.json", "normal_search_result.json", "submission_evidence.json"):
        shutil.copy2(HERE / name, ARCHIVE / ("before_feedback_" + name))
    report = read(PACKAGE / "validation_report.json")
    raw = "可行; 锯切刀数=119268, 成材率=96.71%, 覆盖率=99.98%, 子分(刀/材/覆/时)=75.46/96.71/99.98/100.0"
    provenance = ("User-provided feedback immediately following the submission_normal_985 package in this conversation; "
                  "local file hashes verified. The association is conversational, not a server-side submission receipt.")
    feedback = {
        "source": "User-provided platform result text and leaderboard table in this conversation",
        "raw_result_text": raw,
        "raw_leaderboard_row": "| 35 | AIC-2026-93096493 | 鱼不吃猫 | 2026-09-16 10:02:46 | 89.1900 | 2026-09-16 10:02:54 |",
        "feasible": True,
        "total_score_display": "89.1900",
        "rank_display": 35,
        "competition_id": "AIC-2026-93096493",
        "team": "鱼不吃猫",
        "submitted_at_display": "2026-09-16 10:02:46",
        "evaluated_at_display": "2026-09-16 10:02:54",
        "display_timezone": "Asia/Shanghai",
        "knives": 119268,
        "yield_percent": 96.71,
        "coverage_percent": 99.98,
        "subscores": {"knives": 75.46, "material": 96.71, "coverage": 99.98, "time": 100.0},
        "source_folder": "submission_normal_985",
        "json_sha256": sha(source_json),
        "zip_sha256": sha(source_zip),
        "association_provenance": provenance,
        "server_submission_receipt_available": False,
        "local_metrics_at_submission": report["after"],
        "local_prediction_status": "severely_miscalibrated_do_not_use_for_official_score_claims",
        "regression_vs_best_user_reported_official": {"previous_best_score": 96.49, "score_difference": -7.30},
        "official_minus_local_knives": 119268 - report["after"]["knives"],
        "diagnosis_status": "Knife-count discrepancy established; root cause not yet established by this archive",
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write(PACKAGE / "official_feedback.json", feedback)
    (PACKAGE / "DO_NOT_RESUBMIT.md").write_text(
        "# 已确认退步的提交包，请勿作为最佳方案重复提交\n\n"
        "用户反馈：官方可行，但总分 **89.19**，刀数 **119268**，成材率 **96.71%**。"
        "原本地估分98.6325、91596刀与官方严重失配，不能作为官方提升的依据。\n\n"
        "当前已知官方最佳仍是 `submission_optimized/初赛结果_棒材优化.zip` 的96.49分。"
        "此文件夹保留用于复现、诊断；JSON和ZIP均未修改。\n\n"
        "反馈与文件的关联来自会话顺序和本地哈希，并非服务器回执。"
        "详见 `official_feedback.json` 和 `../evidence/20260916/EVIDENCE.md`。\n",
        encoding="utf-8",
    )
    files = []
    for name in ("初赛结果_棒材优化.json", "初赛结果_棒材优化.zip", "validation_report.json", "提交说明.md", "official_feedback.json", "DO_NOT_RESUBMIT.md"):
        src = PACKAGE / name
        dst = ARCHIVE / name
        shutil.copy2(src, dst)
        files.append({"original_path": src.relative_to(ROOT).as_posix(),
                      "archive_path": dst.relative_to(HERE).as_posix(),
                      "sha256": sha(dst), "bytes": dst.stat().st_size})
    write(HERE / "official_feedback_89_19.json", feedback)
    evidence = read(HERE / "submission_evidence.json")
    evidence["candidate_records"].append({
        "id": "official_89_19", "source_folder": "submission_normal_985",
        "json_sha256": feedback["json_sha256"], "zip_sha256": feedback["zip_sha256"],
        "official_feedback": feedback, "official_status": "user_reported_feasible_score_regression",
        "local_metrics_after_packaging": report["after"],
        "local_independent_check": report["independent_platform_check"],
        "production": report["production"], "files": files, "association_provenance": provenance,
    })
    write(HERE / "submission_evidence.json", evidence)
    status = read(HERE / "stage_status.json")
    status["sequence"][1]["status"] = "official_regression_model_calibration_required"
    status["sequence"][1]["intermediate_target_status"] = "98.6325 local target attained under a severely miscalibrated model; official result89.19"
    status["sequence"][2]["status"] = "paused_until_model_calibrated_and_phase_2_verified"
    status["local_985_milestone"]["official_acceptance_verified"] = True
    status["local_985_milestone"]["official_verification_source"] = "user report with conversational file association, no server receipt"
    status["local_985_milestone"]["official_score"] = 89.19
    status["local_985_milestone"]["prediction_validity"] = "invalid_for_official_improvement_claims"
    status["normal_99_search"]["state"] = "completed_candidate_official89_19_model_mismatch"
    status["normal_99_search"]["official_feedback_required"] = False
    status["normal_99_search"]["official_99_reached"] = False
    status["normal_99_search"]["calibration_required"] = True
    status["latest_user_steering"] = raw + "; leaderboard score89.1900 rank35"
    status["latest_official_feedback"] = feedback
    status["current_action"] = "Diagnose knife accounting before further score-directed search; preserve official96.49 package"
    status["known_limits"].append("Third user-reported official evaluation: local91596 knives versus official119268. Earlier local score improvements are not reliable official progress.")
    write(HERE / "stage_status.json", status)
    result = read(HERE / "normal_search_result.json")
    result["official_acceptance_verified"] = True
    result["official_feedback"] = feedback
    result["local_score_prediction_validity"] = "severely_miscalibrated"
    result["official_99_reached"] = False
    write(HERE / "normal_search_result.json", result)
    md = (HERE / "EVIDENCE.md").read_text(encoding="utf-8-sig")
    md = md.replace("# 2026-09-16 证据与阶段状态\n", "# 2026-09-16 证据与阶段状态\n\n"
        "**最新反馈：本地98.6325分包，官方仅89.19分。** 用户报告官方可行，刀数119268、成材率96.71%、覆盖率99.98%；"
        "本地刀数91596少计了27672刀。正常模型与官方严重失配，98.63只代表旧模型内的估分，不能表示官方进步。"
        "当前已知官方最佳仍为96.49；先修复计数与评分口径，暂停100分评分边界实验。\n\n"
        "[反馈原文、时间与哈希](official_feedback_89_19.json)；[本次提交快照](artifacts/official_89_19/)。"
        "关联来自会话顺序与本地文件哈希，无服务器回执。旧证据全部保留，以下历史状态须结合这条新反馈阅读。\n")
    md = md.replace("| 最新继续搜索包 | 尚无反馈 | — | — | 92043 | 95.86837% | 97.868663 |", "| 最新继续搜索包 | 尚无反馈 | — | — | 92043 | 95.86837% | 97.868663 |\n"
        "| 本地98.63包（明显退步） | **89.19** | **119268** | **96.71%** | 91596 | 97.77835% | 98.632530（失准） |")
    md = md.replace("目前只有两个官方样本", "目前有三个用户转述的官方样本")
    md = md.replace("且刀数子分都低于100", "且刀数子分都低于100")
    md = md.replace("官方结果尚未知，100分边界阶段未开始。", "当时官方结果尚未知；现收到官方89.19分反馈，本地估分失准，100分边界阶段暂停。")
    md += ("\n## 10:02:54 官方退步反馈\n\n"
           "官方子分（刀/材/覆/时）为75.46/96.71/99.98/100.0，榜单第35名，分数89.1900。"
           "提交时间2026-09-16 10:02:46，打分时间10:02:54，时区Asia/Shanghai。\n\n"
           "本次JSON SHA256：`" + EXPECTED_JSON_SHA256 + "`。"
           "官方反馈确认可行性，但否定了把此前本地高分当作官方改善的推断。计刀差异原因仍待代码与规则诊断；"
           "存档不把任何未验证解释当成事实。旧提交文件和原校验报告原样保留；生成时的报告状态不回写。\n")
    (HERE / "EVIDENCE.md").write_text(md, encoding="utf-8")
    assert sha(source_json) == EXPECTED_JSON_SHA256
    assert sha(source_zip) == feedback["zip_sha256"]
    print(json.dumps({"archived": str(ARCHIVE), "json_sha256": sha(source_json), "score": 89.19,
                      "official_best_retained": status["current_verified_official_best"]["score"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
