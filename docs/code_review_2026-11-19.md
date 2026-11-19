# OBI 코드 검토 (2026-11-19)

대상: `app.py`, `obi_preprocess.py`, `obi_criteria.py`, `obi_ai.py`, `obi_store.py` 전체
검증: 합성 DDI 로그(`tests/fixtures/sample_ddi.py`)로 앱과 같은 경로를 실행해 재현 확인

## 1. 이번에 수정한 것 (하네스 준비에 필요했던 것)

| # | 파일 | 문제 | 영향 | 조치 |
|---|---|---|---|---|
| F1 | obi_preprocess.py `conv_print` | `출력시간(PC)`가 빈 칸이면 NaN(참)이라 `or` 가 서버 시간으로 넘어가지 않음 | 해당 출력 이벤트 시간이 NaT → 검증결과 "시간 해석 실패" | PC 시간이 NaT 일 때 서버 시간 사용 |
| F2 | obi_ai.py `mock_analyze/_summary` | 시간 NaT 인 이벤트가 '확인 필요'면 `strftime` 예외 | **상세 화면 전체가 오류로 안 열림** | `_fmt_time()` → "시간 미상" 표시 |
| F3 | obi_criteria.py `apply_criteria` | 수신 도메인을 `set` 으로 순회 | B2/R2/R3 사유 문구 순서가 실행마다 달라질 수 있음 | 정렬 후 순회 |
| F4 | obi_ai.py `mock_analyze` | 위험점수 정렬이 불안정 정렬 | 동점 후보 순서가 보장되지 않음 | `kind="stable"` |
| F5 | obi_store.py `_conn` | `with sqlite3.connect()` 는 커밋만 하고 닫지 않음 | Windows 에서 DB 파일 잠금 누적 | contextmanager 로 커밋 후 close |
| F6 | obi_preprocess.py `LogWorkbook` | `pd.ExcelFile` 을 닫지 않음 | 업로드 임시 파일 잠금 → 임시 폴더 정리 시 PermissionError 가능 | `with pd.ExcelFile(...)` |

구조 변경: `app.run_analysis` 의 비화면 로직을 `obi_pipeline.analyze_file()` 로, 후보·요약 생성을 `obi_pipeline.derive()` 로 옮김.
앱과 하네스가 같은 코드를 지나가므로 "하네스는 통과했는데 화면은 다르다"가 생기지 않는다.

## 2. 남은 이슈 (오후 개발 때 우선순위 정해서 처리)

> 2026-11-19 오후 요구사항 반영으로 해결: **H1**(P-3), **H2**(P-1), **M3**(REQ-04 floor 반올림), **L1**(REQ-01 라벨 상수화).
> 나머지는 아래 그대로 남아 있다.

수정하면 골든 결과가 바뀌는 항목은 하네스 diff 로 영향 범위를 확인한 뒤 `update` 한다.

### 높음
- **H1. 사건 묶기 오탐** — `obi_ai._cluster` 가 `_stem(대상)` 으로 같은 파일을 찾는데, 웹 이벤트의 대상은 URL 이라
  `mybox.naver.com/upload` 와 `dropbox.com/upload` 가 "upload" 로 같은 사건이 됨(합성 샘플 C03 에서 재현).
  메일은 "제목: …" 이 키가 됨. → 파일 채널만 대상으로 하거나 `파일목록` 기반 `file_key` 사용 권장.
- **H2. `파일목록` 이 저장되지 않음** — 전처리 출력 컬럼(`cols`)에 없어 DB 저장 후 타임라인에서 사라짐.
  LLM 연동 시 첨부 파일명 목록이 필요하면 `상세` 문자열을 다시 파싱해야 함. 저장 컬럼에 추가 검토.
- **H3. 외부 LLM 전송 데이터** — 'AI 분석' 탭은 이름·부서·계정·파일명·수신자를 프롬프트에 담아 OpenAI/Anthropic 으로 보낼 수 있음.
  퇴직자 개인정보·사내 파일명이 외부로 나가므로 사용 승인 범위 확인, 필요 시 가명화 옵션 또는 외부 프로바이더 경고 문구.

### 중간
- **M1. DLP 필수 컬럼 누락 시 원시 KeyError** — `conv_dlp` 가 `ATTACHED FILE`, `BLOCKEDSTATUS` 등을 바로 참조.
  헤더 식별 컬럼(`INCIDENT ID`)만 확인하므로, 형식이 조금 다른 추출본은 "분석 중 오류: 'ATTACHED FILE'" 로 끝남.
  → 시트별 필수 컬럼 검사 후 `시트상태` 에 "형식 확인 필요" 로 표시.
- **M2. 키워드 부분일치 오탐** — `MATERIAL_KEYWORDS` 의 `Plan`·`AOP` 가 대소문자 무시 부분일치라 `explanation`, `plant`, `Planet` 도 B3.
  영문 키워드는 단어 경계(`\b`) 매칭 검토.
- **M3. 점수 반올림** — `round()` 는 은행가 반올림이라 32.5→32, 37.5→38 처럼 들쭉날쭉. 밴드 경계(40·70) 근처에서 체감됨. `floor(x+0.5)` 권장.
- **M4. LLM 오류 처리** — 429/500 은 `raise_for_status` 의 HTTPError, 타임아웃은 ReadTimeout 으로 "AI 연동 오류: …" 로만 표시. 응답 본문(사유)이 안 보임.
- **M5. 라이브러리에서 `SystemExit`** — `LogWorkbook` 이 SystemExit 을 던지고 app 이 잡는 구조. 전용 예외(예: `EncryptedWorkbookError`)로 바꾸는 게 안전.

### 낮음
- L1. `CODE_CHECKS` B6 이름 "단일 출력 50매 이상" 이 하드코딩 — `PRINT_PAGES_MIN` 을 바꿔도 라벨은 50 (하네스로 확인).
- L2. `SECURITY_KEYWORDS` 에 `Confidential`/`CONFIDENTIAL` 중복 → 대소문자 무시 매칭이라 사유에 "(Confidential, CONFIDENTIAL)" 두 번 표시.
- L3. 수신자가 비어 있는 메일도 R6 "사내 수신만" 으로 분류.
- L4. 프린터명이 빈 칸이면 `"nan"` 문자열이 상세·최초등장 비교 키에 들어감.
- L5. `mock_insights`·`WEIGHTS` 설명의 "최근 30일" 이 `RECENT_DAYS` 와 연동되지 않음.
- L6. 사이드바 `text_input` 에 `key` 와 `value` 를 함께 지정 → Streamlit 경고 가능. 기본값은 `session_state.setdefault` 로.
- L7. `unsafe_allow_html` 에 사용자 입력(이름·부서) 직접 삽입 — 내부 도구라 위험은 낮지만 `html.escape` 권장.
- L8. 성능: USB×DLP USB 이중 `iterrows`, 최초등장 `iterrows` — 6개월 웹 로그(수만~수십만 행)에서 느려질 수 있음.
