# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**OBI (Offboarding Inspector)** — a Streamlit web app for analyzing departing-employee security logs (DLP, web, USB, print) extracted by DDI. It applies rule-based checks and a mock AI scorer that is designed to be replaced with a real LLM.

## Running the app

```bash
streamlit run app.py
```

Or double-click `run_obi.bat` (installs missing packages; creates `obi.ini` from the example on first run).

### Local config / login (`obi.ini`, gitignored)

`obi_config.py` reads `obi.ini` (or `OBI_CONFIG_PATH`); template is `obi.ini.example`. `[auth] password` must be exactly 4 digits — missing file or bad value **fails closed** (main screen never opens). `app.py:login_gate()` runs before the sidebar; 5 wrong tries → 30 s lock (per session). `[ollama] models` = allowed models; the sidebar shows only those also in `ollama list` (`obi_ai.list_ollama_models`, GET `/api/tags`; empty = all installed). `[openai]` = the only external API (OpenAI `/chat/completions` format). Tests use a temp `obi.ini` (password `1234`, autouse fixture in `tests/conftest.py`) and start app sessions with `obi_authed=True`.

The preprocessor can also be run standalone (useful for batch/debugging):

```bash
python obi_preprocess.py 입력.xlsx 출력.xlsx --retire-date 2026-09-30 [--base-date 2026-09-02] [--account kildong.hong]
```

## Harness (same input → same output) — run on every change

```bash
python -m pytest                      # golden + contracts + scenarios (REQ 완료 기준) + units + store + LLM parsers + app smoke
python tests/harness.py check         # golden diff only, human-readable
python tests/harness.py update        # after an INTENDED output change → review `git diff tests/golden`
python tests/harness.py snapshot 실제로그.xlsx --retire-date 2026-09-30 --name 홍길동   # local baseline (tests/local/, gitignored)
python tests/harness.py compare  실제로그.xlsx --retire-date 2026-09-30 --name 홍길동   # check refactors against real data
```

Development rules:
- Input is `tests/fixtures/sample_ddi.py` (synthetic, built at test time; `.xlsx` is never committed). New rule/edge case → add rows there + a line in `SCENARIO_NOTES`, then `update`.
- Golden snapshots in `tests/golden/sample/`: `schema.json` (columns = **format**), `excel.json` (exported workbook), `app.json` (after DB round-trip, what the UI shows), `prompt.txt` (LLM prompt). A diff in `schema.json` means the format changed — call it out explicitly.
- Never `update` golden to make a failing test pass without explaining the diff to the user.
- `tests/test_scenarios.py` encodes each requirement's 완료 기준 (one test group per REQ; the requirement docs themselves are no longer kept in the repo). New requirement → add its scenario there.
- `tests/test_contracts.py` holds invariants that must survive the LLM swap (candidate columns, 판단 keys = `WEIGHTS`, score = `score_candidate(판단)`, every `확인 필요` event in exactly one candidate). LLM reports must pass `harness.check_report_format()` (= `obi_ai.check_report_format`, also run on every AI 의견 in the app).
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
obi_config.py     — local obi.ini: login password, Ollama allowed models, OpenAI-format API
obi_pipeline.py   — headless logic shared by app and harness: read_log/analyze_file/save, derive (candidates),
                    key_files (REQ-03), personal_mail (REQ-05), dashboard (REQ-02), board (REQ-06), review_missing (REQ-08)
obi_charts.py     — Altair charts for the dashboard (no streamlit; fixed channel colors)
obi_report.py     — report on demand: build_report() (content) / to_xlsx() (format) (REQ-07)
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

### AI 의견 (LLM free analysis, fixed output frame)

The harness fixes what the **code** produces (candidates, grades, checks). The LLM 'AI 분석' opinion is free analysis: `obi_ai.build_llm_prompt(info, crit, cands, tl, raw, max_chars)` sends 1부 code results (reference only) + 2부 the full 통합타임라인 (CSV) + 3부 the original DDI sheets (`원본로그`, stored at analysis time by `prep.raw_sheets()` → DB `원본로그_json`; old rows have none). Over `max_prompt_chars` rows are picked deterministically: 확인 필요 → 참고 → their raw rows → the rest (newest first), and each table says `전체 N행 중 M행`. Same input → same prompt (golden `prompt.txt`).
Only the **frame** is fixed: `REPORT_SECTIONS` (5 headings, in order), first line of `5. 최종 위험 수준` = Critical/Medium/Low, cited `E####` must exist, 핵심 우려 사항 must cite an event when there are 확인 필요 events. `stream_analyze(cfg, prompt)` takes the exact prompt shown in the UI. Tuning in `obi.ini`: `[ollama] num_ctx`, `[llm] max_prompt_chars / temperature / timeout`.

### Check code system

Defined entirely in `obi_criteria.py`:

- **B1–B10** — `확인 필요` (must review): security-keyword files, personal-mail attachments, material-keyword exfiltration, personal cloud, USB, large/repeat printing, ambiguous filenames, large transfers (B10). Codes can be two digits — always parse with `obi_criteria.codes_of()`, never substring checks.
- **R1–R6** — `참고 관찰` (reference, not flagged): blocked by DLP, co-internal recipients, public-sector domains, personal documents, recruitment sites, approved services.
  R2 (REQ-11) = external recipient (personal *or* vendor) + an `INTERNAL_DOMAINS` address (sender's own address excluded, `COMPANION_EXCLUDE_SENDER`). Such mail gets no B2/B3/B9/B10; B1 stays (`COMPANION_DOWNGRADE_B1`). A case lowers `행위 맥락` only when *all* its events are R2 mails.
- **A1–A7** — reserved for LLM (currently shown as "LLM 연동 후 판단" in output).

### Operator-configurable values

Edit the top sections of these files to tune for the deployment environment:

- `obi_criteria.py` — keyword lists (`SECURITY_KEYWORDS`, `MATERIAL_KEYWORDS`, …), domain lists (`INTERNAL_DOMAINS`, `PERSONAL_CLOUD_DOMAINS`, …), and numeric thresholds (`PRINT_PAGES_MIN`, etc.).
- `obi_preprocess.py` — `WORK_START_HOUR/END_HOUR`, `ANALYSIS_DAYS`, `RECENT_DAYS`, `HOLIDAYS`, `USB_LINK_SECONDS`, `DLP_MERGE_SECONDS`.

### Storage

SQLite at `./data/obi_history.db` by default. Override with the `OBI_DB_PATH` environment variable. Timeline and criteria tables are stored as JSON blobs. Do **not** place the DB file in OneDrive/SharePoint sync folders — concurrent sync can corrupt it.

Security pledge PDFs (REQ-12) are stored under `OBI_FILES_DIR` (default `./data/pledges/`, gitignored). Same rule: **never in OneDrive/SharePoint sync folders** — they contain signatures and personal data, and sync can leave the folder out of step with the `pledges` table. Server-side file names are generated (`<이력id>_<일시>_<sha앞8>.pdf`); the uploader's file name is only stored in the DB. Re-upload adds a row (latest = current); delete is a mark (`mark_pledge_deleted`), files are removed only when a Test analysis is hard-deleted. Tests always point `OBI_FILES_DIR`/`OBI_DB_PATH` at temp folders (autouse fixture in `tests/conftest.py`).

### Encrypted Excel handling

`LogWorkbook` in `obi_preprocess.py` detects the file type by magic bytes. Non-`PK` files that also fail `xlrd` are opened via `win32com.client.DispatchEx("Excel.Application")` — this requires a licensed Excel installation and `pywin32` on the same Windows machine.
