# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**OBI (Offboarding Inspector)** — a Streamlit web app for analyzing departing-employee security logs (DLP, web, USB, print) extracted by DDI. It applies rule-based checks and a mock AI scorer that is designed to be replaced with a real LLM.

## Running the app

```bash
streamlit run app.py
```

The preprocessor can also be run standalone (useful for batch/debugging):

```bash
python obi_preprocess.py 입력.xlsx 출력.xlsx --retire-date 2026-09-30 [--base-date 2026-09-02] [--account kildong.hong]
```

## Harness (same input → same output) — run on every change

```bash
python -m pytest                      # 51 tests: golden + contracts + units + LLM stream parsers + app smoke
python tests/harness.py check         # golden diff only, human-readable
python tests/harness.py update        # after an INTENDED output change → review `git diff tests/golden`
python tests/harness.py snapshot 실제로그.xlsx --retire-date 2026-09-30 --name 홍길동   # local baseline (tests/local/, gitignored)
python tests/harness.py compare  실제로그.xlsx --retire-date 2026-09-30 --name 홍길동   # check refactors against real data
```

Development rules:
- Input is `tests/fixtures/sample_ddi.py` (synthetic, built at test time; `.xlsx` is never committed). New rule/edge case → add rows there + a line in `SCENARIO_NOTES`, then `update`.
- Golden snapshots in `tests/golden/sample/`: `schema.json` (columns = **format**), `excel.json` (exported workbook), `app.json` (after DB round-trip, what the UI shows), `prompt.txt` (LLM prompt). A diff in `schema.json` means the format changed — call it out explicitly.
- Never `update` golden to make a failing test pass without explaining the diff to the user.
- `tests/test_contracts.py` holds invariants that must survive the LLM swap (candidate columns, 판단 keys = `WEIGHTS`, score = `score_candidate(판단)`, every `확인 필요` event in exactly one candidate). LLM reports must pass `harness.check_report_format()`.
- Output must not depend on set/dict iteration order or wall-clock time (`test_deterministic_across_processes` runs with two `PYTHONHASHSEED`s).
- `obi_pipeline.py` is the single headless path used by both `app.py` and the harness — put non-UI logic there, not in `app.py`.

## Dependencies

Core: `streamlit`, `pandas`, `openpyxl`, `xlrd`  
For encrypted (DRM/sensitivity-labeled) Excel files on Windows: `pywin32` — the app uses COM automation (`win32com.client`) to open them through the installed Excel application.

## Architecture

```
app.py           — Streamlit UI: page_new() / page_history() / render_detail()
obi_preprocess.py — DDI log → unified timeline + verification checks
obi_criteria.py   — check rules (B/R codes) + keyword/domain dictionaries
obi_ai.py         — mock risk scorer; main LLM integration point
obi_store.py      — SQLite persistence (data/obi_history.db)
obi_pipeline.py   — headless path shared by app and harness: analyze_file() / derive()
tests/            — harness.py (golden runner), fixtures/sample_ddi.py, golden/, test_*.py
```

### Data flow

1. User uploads a DDI-extracted Excel → `app.py:run_analysis()`
2. `obi_preprocess.run()` reads each sheet, normalises rows into a unified timeline, flags cross-channel file links, and applies `obi_criteria.apply_criteria()` which stamps each event with a check result (`확인 필요` / `참고` / blank) and reason codes.
3. The result is saved to SQLite via `obi_store.save_analysis()`.
4. `obi_ai.mock_analyze()` clusters `확인 필요` events into candidate incidents and scores them.
5. `render_detail()` displays the four tabs: 검토 후보 / 기준 점검 / 통합타임라인 / 채널·추이.

### Risk scoring model

`WEIGHTS` in `obi_ai.py` defines five factors (AI 65 pts, code 35 pts):

| Factor | Owner | Max pts |
|---|---|---|
| 자료 민감도 | AI | 25 |
| 행위 맥락 | AI | 25 |
| 흐름 연결 | AI | 15 |
| 반출 경로 | Code | 20 |
| 시점·패턴 | Code | 15 |

Each factor is rated 높음/중간/낮음 (1.0 / 0.5 / 0.0), summed with `floor(x+0.5)`. **Users never see the number** (REQ-04): it is mapped to a grade by `grade_of()` — ≥70 Critical, ≥40 Medium, else Low (`GRADE_*_MIN`), and B2 (personal-mail attachment) lifts a case to at least Medium, to Critical with B1/B3/B10 (`PERSONAL_MAIL_GRADE_BOOST`). `위험점수` stays only as an internal sort key; never put it in UI, exports, insights or the LLM prompt.

### LLM integration point

`obi_ai.mock_analyze()` is the only function to replace when wiring in a real LLM. It must return a DataFrame with columns: `후보ID`, `등급`, `위험점수`, `유형`, `기간`, `채널`, `건수`, `설명`, `근거이벤트`, `점검항목`, `판단` (dict of factor→level), `근거` (dict of factor→reason string), `구분`.

`mock_insights()` (the 한눈에 보기 summary) should also be replaced; it produces `(lead_text: str, points: list[str])`.

### Check code system

Defined entirely in `obi_criteria.py`:

- **B1–B10** — `확인 필요` (must review): security-keyword files, personal-mail attachments, material-keyword exfiltration, personal cloud, USB, large/repeat printing, ambiguous filenames, large transfers (B10). Codes can be two digits — always parse with `obi_criteria.codes_of()`, never substring checks.
- **R1–R6** — `참고 관찰` (reference, not flagged): blocked by DLP, co-internal recipients, public-sector domains, personal documents, recruitment sites, approved services.
- **A1–A7** — reserved for LLM (currently shown as "LLM 연동 후 판단" in output).

### Operator-configurable values

Edit the top sections of these files to tune for the deployment environment:

- `obi_criteria.py` — keyword lists (`SECURITY_KEYWORDS`, `MATERIAL_KEYWORDS`, …), domain lists (`INTERNAL_DOMAINS`, `PERSONAL_CLOUD_DOMAINS`, …), and numeric thresholds (`PRINT_PAGES_MIN`, etc.).
- `obi_preprocess.py` — `WORK_START_HOUR/END_HOUR`, `ANALYSIS_DAYS`, `RECENT_DAYS`, `HOLIDAYS`, `USB_LINK_SECONDS`, `DLP_MERGE_SECONDS`.

### Storage

SQLite at `./data/obi_history.db` by default. Override with the `OBI_DB_PATH` environment variable. Timeline and criteria tables are stored as JSON blobs. Do **not** place the DB file in OneDrive/SharePoint sync folders — concurrent sync can corrupt it.

### Encrypted Excel handling

`LogWorkbook` in `obi_preprocess.py` detects the file type by magic bytes. Non-`PK` files that also fail `xlrd` are opened via `win32com.client.DispatchEx("Excel.Application")` — this requires a licensed Excel installation and `pywin32` on the same Windows machine.
