"""Write resources/ground_truth.json from the curated table below and check it against source.

Every entry was checked by hand against the current app source (see plan/notes.md section 8
and plan/gt_review.md).

accepted_lines policy: a line is accepted as an exact hit when (1) it belongs to the injected
change and (2) changing that line alone removes the crash without breaking normal behaviour.
Lines that are related but fail either test go into root_cause_locations only.
Running this script re-verifies that each referenced line exists and prints its content, so a
reviewer can confirm the table without opening every file.

Usage:
    uv run python -m eval.build_ground_truth [--check-only]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .paths import DEFAULT_CASES, DEFAULT_DATA_ROOT, DEFAULT_GROUND_TRUTH

logger = logging.getLogger(__name__)

DATA_ROOT = DEFAULT_DATA_ROOT
OUT = DEFAULT_GROUND_TRUTH

# basis values
EXPLICIT = "test-script annotation"  # file:line written in the official test script / META
DESC = "test-script description"  # file/function named in prose, line chosen by us
REPORT = "B3 injection report/spec"  # docs/ in data_B3, re-checked against current source
SIBLING = "sibling-version diff"  # same app shipped twice with different faults
LOG_RC = "log Root cause section"  # hand-written log that states the answer
ANALYSIS = "manual analysis"  # our own reading of the code


def loc(file: str, start: int, end: int | None = None, role: str = "root_cause") -> dict:
    return {"file": file, "start_line": start, "end_line": end or start, "role": role}


def line(file: str, n: int) -> dict:
    return {"file": file, "line": n}


GT: list[dict] = [
    {
        "case_id": "TC_JSError_002@AccountingApp1@B1",
        "crash": line("components/bill_chart/src/main/ets/components/BillBarChart.ets", 228),
        "accepted_lines": [
            line("components/bill_chart/src/main/ets/components/BillBarChart.ets", 228),
            line("components/bill_chart/src/main/ets/components/BillBarChart.ets", 48),
        ],
        "root_cause_locations": [
            loc("components/bill_chart/src/main/ets/components/BillBarChart.ets", 227, 229, "faulty_call"),
            loc("components/bill_chart/src/main/ets/components/BillBarChart.ets", 48, 48, "data_source"),
        ],
        "basis": [EXPLICIT, ANALYSIS],
        "certainty": "high",
        "notes": "Field _formatterBuilder holds the class itself; calling it without new throws. "
        "Line 184 constructs the same class correctly with new, so line 48 is injected; fixing it "
        "alone also removes the crash (accepted_lines policy).",
    },
    {
        "case_id": "TC_JSError_004@BookRead2@B1",
        "crash": line("feature/book_home/src/main/ets/views/BookViewListPage.ets", 21),
        "accepted_lines": [line("feature/book_home/src/main/ets/views/BookViewListPage.ets", 21)],
        "root_cause_locations": [loc("feature/book_home/src/main/ets/views/BookViewListPage.ets", 21, 22)],
        "basis": [DESC],
        "certainty": "high",
        "notes": "books!.load() runs before books is constructed on the next line.",
    },
    {
        "case_id": "TC_JSError_007@Calendar1@B1",
        "crash": line("components/date_calculation/src/main/ets/utils/Utils.ets", 55),
        "accepted_lines": [
            line("components/date_calculation/src/main/ets/utils/Utils.ets", 55),
            line("components/date_calculation/src/main/ets/utils/Utils.ets", 53),
        ],
        "root_cause_locations": [loc("components/date_calculation/src/main/ets/utils/Utils.ets", 51, 55)],
        "basis": [DESC],
        "certainty": "high",
        "notes": "days is created as BigInt (53) and passed to Math.floor (55).",
    },
    {
        "case_id": "TC_JSError_001@ComprehensiveTool1@B1",
        "crash": line("components/money_track/src/main/ets/viewmodels/ResourceManageVM.ets", 83),
        "accepted_lines": [line("components/money_track/src/main/ets/viewmodels/ResourceManageVM.ets", 78)],
        "root_cause_locations": [
            loc("components/money_track/src/main/ets/viewmodels/ResourceManageVM.ets", 78, 80, "missing_check")
        ],
        "basis": [EXPLICIT],
        "certainty": "high",
        "notes": "Bounds check covers index1 only; drop on the extra 'add' grid cell gives index2 == length.",
    },
    {
        "case_id": "TC_JSError_009@EnterpriseRecruitment2@B1",
        "crash": line("commons/component/src/main/ets/utils/PropUtil.js", 3),
        "accepted_lines": [
            line("product/phone/src/main/ets/pages/jobRoute/JobDelivery.ets", 186),
            line("commons/component/src/main/ets/utils/PropUtil.js", 3),
        ],
        "root_cause_locations": [
            loc("product/phone/src/main/ets/pages/jobRoute/JobDelivery.ets", 183, 186, "faulty_call"),
            loc("commons/component/src/main/ets/utils/PropUtil.js", 2, 4, "crash_site"),
        ],
        "basis": [DESC, ANALYSIS],
        "certainty": "high",
        "notes": "find() returns undefined before the resume is submitted; hasKey uses 'in' on it. "
        "hasKey's only caller is line 186, so PropUtil.js is part of the injection and a null "
        "guard there alone also fixes the crash. Reviewed 2026-10-19.",
    },
    {
        "case_id": "TC_JSError_010@Express1@B1",
        "crash": line("features/business_home/src/main/ets/viewModels/ExpressPageVM.ets", 354),
        "accepted_lines": [
            line("features/business_home/src/main/ets/viewModels/ExpressPageVM.ets", 346),
            line("features/business_home/src/main/ets/viewModels/ExpressPageVM.ets", 354),
        ],
        "root_cause_locations": [
            loc("features/business_home/src/main/ets/viewModels/ExpressPageVM.ets", 345, 346, "faulty_condition"),
            loc("features/business_home/src/main/ets/viewModels/ExpressPageVM.ets", 349, 354, "crash_site"),
        ],
        "basis": [DESC, ANALYSIS],
        "certainty": "high",
        "notes": "'DAILY_PICKUP ||' lets the default template (pickupTime '') into the parser; "
        "''.split(' - ') has one part, so timeParts[1] is out of bounds. Confirmed by the test "
        "script's fault chain. Reviewed with the user 2026-10-19.",
    },
    {
        "case_id": "TC_JSError_005@MoneyTrack1_2@B1",
        "crash": line("features/assets/src/main/ets/viewmodels/AssetAnalysisVM.ets", 114),
        "accepted_lines": [line("features/assets/src/main/ets/viewmodels/AssetAnalysisVM.ets", 114)],
        "root_cause_locations": [loc("features/assets/src/main/ets/viewmodels/AssetAnalysisVM.ets", 109, 115)],
        "basis": [EXPLICIT, SIBLING],
        "certainty": "high",
        "notes": "Log reports line 110 but the statement is at 114 in the shipped source (offset +4).",
    },
    {
        "case_id": "TC_JSError_025@Music1@B1",
        "crash": line("components/module_musicplay/src/main/ets/components/PlayerControlArea.ets", 190),
        "accepted_lines": [line("components/module_musicplay/src/main/ets/components/PlayerControlArea.ets", 190)],
        "root_cause_locations": [
            loc("components/module_musicplay/src/main/ets/components/PlayerControlArea.ets", 187, 192)
        ],
        "basis": [DESC],
        "certainty": "high",
        "notes": "remainMs == 0 at the slider end -> log10(Infinity) -> repeat(Infinity).",
    },
    {
        "case_id": "TC_JSError_006@Notes1@B1",
        "crash": line("common/datasource/src/main/ets/NoteService.ets", 287),
        "accepted_lines": [
            line("common/datasource/src/main/ets/NoteService.ets", 197),
            line("common/datasource/src/main/ets/NoteService.ets", 237),
        ],
        "root_cause_locations": [
            loc("common/datasource/src/main/ets/NoteService.ets", 195, 198, "data_source"),
            loc("common/datasource/src/main/ets/NoteService.ets", 235, 238, "data_source"),
            loc("components/richeditor/src/main/ets/model/Note.ets", 57, 60, "data_source"),
        ],
        "basis": [DESC, ANALYSIS],
        "certainty": "high",
        "notes": "note.noteService = this creates a cycle; JSON.stringify(notes) at 287 then throws. "
        "Test-script trigger (new note -> save) goes through addNote (197); edits go through "
        "updateNote (237). The noteService field has no other consumer. Line 287 is the template's "
        "own debug log, so it is not accepted. Reviewed 2026-10-19.",
    },
    {
        "case_id": "TC_JSError_026@Teleprompter1@B1",
        "crash": line("components/teleprompter/src/main/ets/components/ConfigSheet.ets", 232),
        "accepted_lines": [
            line("components/teleprompter/src/main/ets/components/ConfigSheet.ets", 232),
            line("components/teleprompter/src/main/ets/components/ConfigSheet.ets", 231),
        ],
        "root_cause_locations": [loc("components/teleprompter/src/main/ets/components/ConfigSheet.ets", 230, 232)],
        "basis": [ANALYSIS],
        "certainty": "high",
        "notes": "toFixed(level) with level > 100.",
    },
    {
        "case_id": "TC_JSError_011@ChildrenEducation1@B2",
        "crash": line("commons/utils/src/main/ets/player/VideoPlayPage.ets", 72),
        "accepted_lines": [
            line("commons/utils/src/main/ets/player/VideoPlayPage.ets", 72),
            line("commons/utils/src/main/ets/player/VideoPlayPage.ets", 71),
        ],
        "root_cause_locations": [loc("commons/utils/src/main/ets/player/VideoPlayPage.ets", 70, 75)],
        "basis": [LOG_RC],
        "certainty": "high",
        "notes": "Class called without new via an ESObject alias. The log itself states the answer.",
    },
    {
        "case_id": "TC_JSError_001@ComprehensiveMall1@B2",
        "crash": line("features/order/src/main/ets/views/OrderInfoPage.ets", 119),
        "accepted_lines": [line("features/order/src/main/ets/views/OrderInfoPage.ets", 119)],
        "root_cause_locations": [loc("features/order/src/main/ets/views/OrderInfoPage.ets", 117, 119)],
        "basis": [EXPLICIT],
        "certainty": "high",
        "notes": "Guard checks orderItem?.logisticInfo but the body dereferences vm.logisticInfo!.",
    },
    {
        "case_id": "TC_JSError_002@ComprehensiveNews1@B2",
        "excluded": "fault not present in shipped source: HistoryVM.toDeleteList is never null and the "
        "logged line 48 is '} else {'. The log in this case's own folder belongs to TC_JSError_003.",
        "basis": [ANALYSIS],
        "certainty": "n/a",
    },
    {
        "case_id": "TC_JSError_003@ComprehensiveNews1@B2",
        "crash": line("commons/lib_news_feed_details/src/main/ets/views/ArticleFeedDetails.ets", 111),
        "accepted_lines": [line("commons/lib_news_api/src/main/ets/httpsmock/mockdata/MockArticle.ets", 50)],
        "root_cause_locations": [
            loc("commons/lib_news_api/src/main/ets/httpsmock/mockdata/MockArticle.ets", 50, 50, "data_source"),
            loc("commons/lib_news_feed_details/src/main/ets/views/ArticleFeedDetails.ets", 111, 111, "crash_site"),
        ],
        "basis": [ANALYSIS],
        "certainty": "high",
        "notes": "article_2 has authorId 'Author_2' but the author mock uses 'author_2' (case mismatch), "
        "so queryAuthorInfo returns undefined and author is never set. It is the only article whose "
        "authorId matches no author. Line 111 is not accepted: unguarded author access is common in "
        "the template, and adding ?. there would hide the wrong author. The case's test script "
        "describes a different app. Reviewed 2026-10-19.",
    },
    {
        "case_id": "TC_JSError_005@MoneyTrack1@B2",
        "crash": line("features/statistics/src/main/ets/components/CalendarSummary.ets", 55),
        "accepted_lines": [
            line("features/statistics/src/main/ets/components/CalendarSummary.ets", 54),
            line("features/statistics/src/main/ets/components/CalendarSummary.ets", 55),
        ],
        "root_cause_locations": [
            loc("features/statistics/src/main/ets/components/CalendarSummary.ets", 53, 56),
            loc("features/statistics/src/main/ets/components/CalendarSummary.ets", 1, 1, "related"),
        ],
        "basis": [SIBLING],
        "certainty": "high",
        "notes": "find(...)! returns undefined for a date with no bill; baseline passes dateStr through.",
    },
    {
        "case_id": "TC_JSRangeError_024@WebShortDrama1@B2",
        "crash": line("components/aggregated_share/src/main/ets/views/ShareView.ets", 40),
        "accepted_lines": [line("components/aggregated_share/src/main/ets/views/ShareView.ets", 40)],
        "root_cause_locations": [
            loc("components/aggregated_share/src/main/ets/views/ShareView.ets", 37, 45),
            loc("components/aggregated_share/src/main/ets/views/ShareView.ets", 100, 103, "related"),
        ],
        "basis": [EXPLICIT, SIBLING],
        "certainty": "high",
        "notes": "META says 40, docstring says 39 (stale); current source has the Uint32Array at 40.",
    },
    {
        "case_id": "TC_JSError_012@HomeStay1.0.6@B3",
        "crash": line("components/travel_discovery/src/main/ets/model/Index.ets", 65),
        "accepted_lines": [line("components/travel_discovery/src/main/ets/utils/MockData.ets", 11)],
        "root_cause_locations": [
            loc("components/travel_discovery/src/main/ets/utils/MockData.ets", 11, 11, "data_source"),
            loc("components/travel_discovery/src/main/ets/pages/PageWaterFlow.ets", 26, 32, "related"),
        ],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "images changed from array to string in mock data; JSON.parse at 65 then fails.",
    },
    {
        "case_id": "TC_JSError_014@DriverLicenseExam1.0.7@B3",
        "crash": line("commons/datasource/src/main/ets/ExamService.ets", 345),
        "accepted_lines": [line("commons/datasource/src/main/ets/ExamService.ets", 345)],
        "root_cause_locations": [loc("commons/datasource/src/main/ets/ExamService.ets", 343, 346)],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "User search text used as a regex source without escaping.",
    },
    {
        "case_id": "TC_JSError_015@FitnessCenter1.0.3@B3",
        "crash": line("components/search/src/main/ets/views/ResultPage.ets", 163),
        "accepted_lines": [line("components/search/src/main/ets/views/ResultPage.ets", 163)],
        "root_cause_locations": [loc("components/search/src/main/ets/views/ResultPage.ets", 162, 168)],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "Malformed named group (?<matched) in a template regex.",
    },
    {
        "case_id": "TC_JSError_016@TouristAttraction1@B3",
        "crash": line("components/module_base/src/main/ets/util/CommonUtil.ets", 10),
        "accepted_lines": [line("components/module_base/src/main/ets/util/CommonUtil.ets", 10)],
        "root_cause_locations": [loc("components/module_base/src/main/ets/util/CommonUtil.ets", 10, 10)],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "Phone regex '(?3|...)' is invalid. Report labels this case TC_JSError_018 (swapped).",
    },
    {
        "case_id": "TC_JSError_017@OfficeAttendance1@B3",
        "crash": line("scenes/agency/src/main/ets/mock/DataManager.ets", 56),
        "accepted_lines": [line("scenes/agency/src/main/ets/mock/DataManager.ets", 56)],
        "root_cause_locations": [loc("scenes/agency/src/main/ets/mock/DataManager.ets", 53, 61)],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "Hard-coded unclosed '(' in new RegExp('(' + keyword).",
    },
    {
        "case_id": "TC_JSError_018@ImageProcessing1@B3",
        "excluded": "no crash log: the archive contains MainEntry.ets instead of a jscrash log.",
        "basis": [REPORT],
        "certainty": "n/a",
        "notes": "Report locates the fault at product/phone/src/main/ets/pages/MainEntry.ets:45-46.",
    },
    {
        "case_id": "TC_JSError_019@Exam1@B3",
        "crash": line("components/select_category/src/main/ets/components/MainPage.ets", 65),
        "accepted_lines": [
            line("features/homePage/src/main/ets/model/TopicModel.ets", 40),
            line("components/select_category/src/main/ets/components/MainPage.ets", 75),
        ],
        "root_cause_locations": [
            loc("features/homePage/src/main/ets/model/TopicModel.ets", 35, 40, "data_source"),
            loc("components/select_category/src/main/ets/components/MainPage.ets", 52, 77, "missing_check"),
        ],
        "basis": [REPORT, ANALYSIS],
        "certainty": "high",
        "notes": "Last subject's nextId points back to the first; recursion has no visited set.",
    },
    {
        "case_id": "TC_JSError_020@TeaDrinkOrders1@B3",
        "crash": line("features/order/src/main/ets/pages/ConfirmOrderPage.ets", 786),
        "accepted_lines": [
            line("features/order/src/main/ets/pages/ConfirmOrderPage.ets", 785),
            line("features/order/src/main/ets/pages/ConfirmOrderPage.ets", 786),
        ],
        "root_cause_locations": [loc("features/order/src/main/ets/pages/ConfirmOrderPage.ets", 783, 786)],
        "basis": [DESC, ANALYSIS],
        "certainty": "high",
        "notes": "Picker options are [无需餐具, 需要餐具依据餐量, 1份, 2份, ...]; index-1 is right for "
        "index >= 2 but the default index 0 (无需餐具, kept by the test script) gives -1 -> "
        "new Array(-1). Fixing 785 (handle 0) or 786 (clamp) alone removes the crash. Reviewed 2026-10-19.",
    },
    {
        "case_id": "TC_JSError_021@AudioEditing1@B3",
        "crash": line("components/audio_common/src/main/ets/viewModel/EditViewModel.ets", 58),
        "accepted_lines": [line("components/audio_common/src/main/ets/viewModel/EditViewModel.ets", 52)],
        "root_cause_locations": [
            loc("components/audio_common/src/main/ets/viewModel/EditViewModel.ets", 52, 52, "off_by_one")
        ],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "Loop condition <= totalFrames reads past the buffer end.",
    },
    {
        "case_id": "TC_JSError_022@HouseholdService1@B3",
        "crash": line("commons/lib_foundation/src/main/ets/http/OrderRequestChecksum.ets", 23),
        "accepted_lines": [
            line("commons/lib_foundation/src/main/ets/http/OrderRequestChecksum.ets", 22),
            line("commons/lib_foundation/src/main/ets/http/OrderRequestChecksum.ets", 23),
        ],
        "root_cause_locations": [
            loc("commons/lib_foundation/src/main/ets/http/OrderRequestChecksum.ets", 21, 23)
        ],
        "basis": [REPORT],
        "certainty": "high",
        "notes": "ArrayBuffer sized in characters, Uint16Array needs 2 bytes per element.",
    },
    {
        "case_id": "TC_JSError_024@WebShortDrama1@B3",
        "crash": line("components/feedback/src/main/ets/utils/FileUtils.ets", 173),
        "accepted_lines": [line("components/feedback/src/main/ets/utils/FileUtils.ets", 173)],
        "root_cause_locations": [
            loc("components/feedback/src/main/ets/utils/FileUtils.ets", 153, 189),
            loc("components/feedback/src/main/ets/pages/FeedbackHistoryPage.ets", 23, 23, "related"),
        ],
        "basis": [SIBLING],
        "certainty": "high",
        "notes": "Int32Array over a buffer whose size is not a multiple of 4.",
    },
]


def check(cases: dict[str, dict], data_root: Path | None = None) -> list[str]:
    """Return problems; print the content of every accepted line for review."""
    problems: list[str] = []
    seen = set()
    for gt in GT:
        cid = gt["case_id"]
        seen.add(cid)
        if cid not in cases:
            problems.append(f"{cid}: not in cases.json")
            continue
        app = (data_root or DATA_ROOT) / cases[cid]["app_dir"]
        refs = [gt["crash"], *gt["accepted_lines"]] if "excluded" not in gt else []
        refs += [{"file": r["file"], "line": n}
                 for r in gt.get("root_cause_locations", [])
                 for n in (r["start_line"], r["end_line"])]
        valid_refs = set()
        for ref in refs:
            path = app / ref["file"]
            if not path.is_file():
                problems.append(f"{cid}: missing file {ref['file']}")
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if not 1 <= ref["line"] <= len(lines):
                problems.append(f"{cid}: line {ref['line']} out of range in {ref['file']}")
            else:
                valid_refs.add((ref["file"], ref["line"]))
        for ref in gt.get("accepted_lines", []):
            if (ref["file"], ref["line"]) not in valid_refs:
                continue
            text = (app / ref["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"{cid:44s} {Path(ref['file']).name}:{ref['line']:<5d} {text[ref['line'] - 1].strip()[:90]}")
    for cid in cases:
        if cid not in seen:
            problems.append(f"{cid}: no ground-truth entry")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cases = {c["case_id"]: c for c in json.loads(args.cases.read_text(encoding="utf-8"))["cases"]}
    problems = check(cases, args.data_root)
    for p in problems:
        logger.error(p)
    if problems:
        raise SystemExit(1)
    if not args.check_only:
        scorable = sum(1 for g in GT if "excluded" not in g)
        payload = {"case_count": len(GT), "scorable_count": scorable, "entries": GT}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("wrote %s: %d entries, %d scorable", args.out, len(GT), scorable)


if __name__ == "__main__":
    main()
