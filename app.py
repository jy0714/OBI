"""
OBI (Offboarding Inspector) 화면 - 1차 버전 (AI 분석 연동 전)

실행: streamlit run app.py
"""
import io
import os
import tempfile
from datetime import date

import pandas as pd
import streamlit as st

import obi_ai as ai
import obi_pipeline as pipeline
import obi_store as store

st.set_page_config(page_title="OBI · Offboarding Inspector", page_icon="🛡️", layout="wide",
                   initial_sidebar_state="expanded")
store.init_db()

NAVY, DEEP, BAR = "#1E2761", "#002060", "#1B3A5C"
BAND_COLOR = {"Critical": ("#FBE9E7", "#A8322D"), "Medium": ("#FDF1DF", "#9A5B0C"), "Low": ("#E9EEF7", "#1E2761")}
GRADE_ICON = {"Critical": "🔴", "Medium": "🟠", "Low": "🟡"}

st.markdown("""
<style>
  .block-container {padding-top: 3.2rem; padding-bottom: 2rem; max-width: 100%;
            padding-left: 2.2rem; padding-right: 2.2rem;}
  .obi-bar {background: linear-gradient(90deg,#1B3A5C 0%,#1E2761 60%,#2B3A80 100%);
            border-radius: 12px; padding: 16px 24px; color: #fff; display: flex;
            align-items: center; gap: 18px; margin-bottom: 18px;
            width: 100%; box-sizing: border-box; overflow: hidden;}
  .obi-logo {font-size: 38px; font-weight: 800; letter-spacing: 3px; line-height: 1;}
  .obi-name {font-size: 15px; font-weight: 600; letter-spacing: .5px;}
  .obi-desc {font-size: 12.5px; opacity: .82; margin-top: 2px;}
  .obi-tag {margin-left: auto; font-size: 12px; background: rgba(255,255,255,.15);
            padding: 6px 12px; border-radius: 20px;}
  .obi-side {text-align:center; padding: 6px 0 12px;}
  .obi-side .l {font-size: 34px; font-weight: 800; color: #1E2761; letter-spacing: 3px;}
  .obi-side .s {font-size: 11.5px; color: #5A6475; letter-spacing: .5px;}
  div[data-testid="stMetric"] {background: #F5F7FC; border: 1px solid #E2E8F0;
            border-radius: 10px; padding: 12px 16px;}
  div[data-testid="stMetricLabel"] p {font-size: 12.5px !important; color: #5A6475 !important;}
  div[data-testid="stMetricValue"] {font-size: 26px !important; color: #1E2761 !important; font-weight: 700;}
  button[data-baseweb="tab"] {font-weight: 600;}
  .obi-chip {display:inline-block; padding: 3px 12px; border-radius: 14px;
            font-size: 12px; font-weight: 700; margin-right: 8px;}
  .obi-card-title {font-size: 16px; font-weight: 700; color: #1A2230;}
  .obi-card-meta {font-size: 12.5px; color: #5A6475; margin-top: 3px;}
  .obi-lead {font-size: 15px; line-height: 1.7;}
</style>
""", unsafe_allow_html=True)


def top_bar(tag):
    st.markdown(f"""<div class="obi-bar">
      <div class="obi-logo">OBI</div>
      <div><div class="obi-name">Offboarding Inspector</div>
      <div class="obi-desc">퇴직자 보안로그 AI 분석 · 기준 점검과 맥락 분석을 함께</div></div>
      <div class="obi-tag">{tag}</div></div>""", unsafe_allow_html=True)


def chip(text, band):
    bg, fg = BAND_COLOR.get(band, BAND_COLOR["Low"])
    return f'<span class="obi-chip" style="background:{bg};color:{fg}">{text}</span>'


COMPANIES = ["DBI", "DBKR"]
REVIEW_STATES = ["검토 전", "검토 중", "검토 완료"]
YES_NO = ["미확인", "예", "아니오"]

# 화면에 기본으로 보여줄 타임라인 컬럼 (나머지는 '전체 컬럼 보기'에서 확인)
TIMELINE_BASIC = ["이벤트ID", "시간", "점검결과", "점검사유", "채널", "행위", "대상", "수신자", "원본시트", "원본행"]


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------
def to_excel_bytes(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
    return buf.getvalue()


def col_is(tl, col, value="Y"):
    return tl[tl[col].fillna("") == value] if col in tl else tl.iloc[0:0]


def col_filled(tl, col):
    return tl[tl[col].fillna("") != ""] if col in tl else tl.iloc[0:0]


def run_analysis(uploaded, info, base_date):
    """업로드 파일을 임시 저장 → 전처리 → 이력 자동 저장 → 임시 파일 삭제"""
    suffix = os.path.splitext(uploaded.name)[1] or ".xlsx"
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input" + suffix)
        out = os.path.join(tmp, "result.xlsx")
        with open(src, "wb") as f:
            f.write(uploaded.getbuffer())
        r = pipeline.analyze_file(src, out, info, base_date)
    return pipeline.save(r)


# ---------------------------------------------------------------------------
# AI 프로바이더 설정 읽기
# ---------------------------------------------------------------------------
def _ai_cfg():
    p = st.session_state.get("ai_provider", "Ollama (로컬)")
    if p == "Ollama (로컬)":
        return {
            "provider": "ollama",
            "model":    st.session_state.get("ollama_model",    ai.PROVIDER_DEFAULTS["ollama"]["model"]),
            "base_url": st.session_state.get("ollama_base_url", ai.PROVIDER_DEFAULTS["ollama"]["base_url"]),
            "api_key":  "",
        }
    if p == "OpenAI 호환":
        return {
            "provider": "openai",
            "model":    st.session_state.get("openai_model",    ai.PROVIDER_DEFAULTS["openai"]["model"]),
            "base_url": st.session_state.get("openai_base_url", ai.PROVIDER_DEFAULTS["openai"]["base_url"]),
            "api_key":  st.session_state.get("openai_api_key", ""),
        }
    return {
        "provider": "anthropic",
        "model":    st.session_state.get("anthropic_model",   ai.PROVIDER_DEFAULTS["anthropic"]["model"]),
        "base_url": ai.PROVIDER_DEFAULTS["anthropic"]["base_url"],
        "api_key":  st.session_state.get("anthropic_api_key", ""),
    }


# ---------------------------------------------------------------------------
# 분석 상세 (새 분석 직후와 이력 조회에서 공통 사용)
# ---------------------------------------------------------------------------
RAW_COLUMNS = ["이벤트ID", "시간", "채널", "행위", "대상", "수신자", "파일목록", "점검사유", "원본시트", "원본행"]


def selectable_table(df, tl, key, ids_col, caption="행을 누르면 원본 로그가 아래에 나옵니다.", height=None):
    """행을 누르면 그 행의 근거 이벤트 원본 로그를 보여 주는 표 (REQ-03·05·09)"""
    opts = dict(hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key)
    if height:
        opts["height"] = height
    event = st.dataframe(df, **opts)
    rows = event.selection.rows if event else []
    if rows:
        picked = df.iloc[rows[0]]
        st.markdown(f"**원본 로그** · {picked[ids_col]}")
        st.dataframe(pipeline.events_of(tl, picked[ids_col]).reindex(columns=RAW_COLUMNS),
                     hide_index=True, width="stretch")
    elif caption:
        st.caption(caption)


def candidate_card(r, tl, key):
    with st.container(border=True):
        st.markdown(chip(f"{GRADE_ICON.get(r.등급, '')} {r.등급}", r.등급) +
                    f'<span class="obi-card-title">{r.유형}</span>'
                    f'<div class="obi-card-meta">{r.기간} · {r.건수}건 · 근거 {r.근거이벤트}</div>',
                    unsafe_allow_html=True)
        st.write(r.설명)
        with st.expander("판단 근거와 원본 로그"):
            st.dataframe(pd.DataFrame(
                [{"판단 요소": k, "주체": ai.WEIGHTS[k][0], "판단": v, "근거": r.근거[k]}
                 for k, v in r.판단.items()]),
                hide_index=True, width="stretch")
            ids = [x.strip() for x in r.근거이벤트.split(",")]
            st.dataframe(tl[tl["이벤트ID"].isin(ids)].reindex(
                columns=["이벤트ID", "시간", "채널", "행위", "대상", "수신자", "점검사유", "원본시트", "원본행"]),
                hide_index=True, width="stretch")


def render_detail(analysis_id, key):
    a = store.get_analysis(analysis_id)
    if a is None:
        st.warning("이력을 찾을 수 없습니다.")
        return
    tl = a["타임라인"]
    crit = a.get("기준점검", pd.DataFrame())
    need = col_is(tl, "점검결과", "확인 필요")
    ref = col_is(tl, "점검결과", "참고")
    cands, lead_text, points = pipeline.derive(tl)
    top_grade = cands["등급"].iloc[0] if not cands.empty else "-"

    # ---------------- 헤더 ----------------
    h1, h2 = st.columns([4, 1])
    h1.markdown(f"<div class='obi-card-title' style='font-size:20px'>{a['이름']} · {a['회사']} {a['부서']}"
                f" · 퇴직일 {a['퇴직일자']}</div>"
                f"<div class='obi-card-meta'>이력 #{a['id']} · 분석 {a['분석일시']} · 로그 기준일 {a['기준일']}"
                f" · 계정 {a['계정']} · 로그 검증 {a['검증상태']}</div>", unsafe_allow_html=True)
    h2.markdown(chip(f"{GRADE_ICON.get(top_grade, '')} 최고 등급 {top_grade}", top_grade), unsafe_allow_html=True)

    pmail = pipeline.personal_mail(tl)
    m = st.columns(6)
    m[0].metric("검토 후보", f"{len(cands)}건")
    m[1].metric("최고 등급", top_grade)
    m[2].metric("확인 필요 행위", f"{len(need)}건")
    m[3].metric("개인메일 발송", f"{len(pmail)}건 (첨부 {int((pmail['첨부 수'] > 0).sum())}건)")
    m[4].metric("참고 관찰", f"{len(ref)}건")
    m[5].metric("전체 이벤트", f"{a['이벤트수']}건")

    if a["검증상태"] != "정상":
        st.error("로그 검증에서 확인이 필요한 항목이 있습니다. '기준 점검' 탭의 검증 상세를 먼저 확인하세요.")

    # ---------------- 요약 ----------------
    with st.container(border=True):
        st.markdown("##### 한눈에 보기 &nbsp;&nbsp; :orange-badge[LLM 연동 전 예시]")
        st.markdown(f"<div class='obi-lead'>{lead_text}</div>", unsafe_allow_html=True)
        for p in points:
            st.markdown(f"- {p}")

    # ---------------- 주요 검토 파일 (REQ-03) ----------------
    files = pipeline.key_files(tl, cands)
    with st.container(border=True):
        st.markdown(f"##### 주요 검토 파일 · {len(files)}개")
        if files.empty:
            st.caption("확인 필요 행위에 등장한 파일이 없습니다.")
        else:
            selectable_table(files, tl, f"{key}_files", "근거 이벤트ID")

    # ---------------- 개인메일 발송 (REQ-05) ----------------
    with st.container(border=True):
        n_att = int((pmail["첨부 수"] > 0).sum())
        st.markdown(f"##### 개인메일 발송 · {len(pmail)}건 &nbsp; :red-badge[첨부 {n_att}건]")
        if pmail.empty:
            st.caption("개인메일 도메인으로 보낸 메일이나 개인메일 사이트 업로드가 없습니다.")
        else:
            view = pmail.assign(첨부=pmail["첨부"].map(lambda x: "📎 첨부" if x else ""))
            selectable_table(view, tl, f"{key}_pmail", "이벤트ID",
                             caption="📎 첨부가 있는 건이 위에 옵니다. 참고(차단·사내 동반·개인 문서)로 분류된 건도 함께 표시합니다. "
                                     "행을 누르면 원본 로그가 나옵니다.")

    t_ai, t_main, t_tl, t_sum, t_llm = st.tabs(
        [f"검토 후보 ({len(cands)})", "기준 점검", "통합타임라인", "채널·추이", "AI 분석"])

    # ---------------- 검토 후보 ----------------
    with t_ai:
        if cands.empty:
            st.info("확인 필요 행위가 없어 후보가 만들어지지 않았습니다.")
        else:
            bands = [(g, cands[cands["등급"] == g]) for g in ai.GRADES]
            tabs = st.tabs([f"{GRADE_ICON[name]} {name} {len(df)}" for name, df in bands])
            for tab, (name, df) in zip(tabs, bands):
                with tab:
                    if df.empty:
                        st.caption("해당 없음")
                    for r in df.itertuples():
                        candidate_card(r, tl, key)
            st.caption("등급: AI 판단(자료 민감도·행위 맥락·흐름 연결)과 코드 확인(반출 경로·시점·패턴)을 종합해 "
                       "Critical / Medium / Low 로 표시합니다.")

    # ---------------- 기준 점검 ----------------
    with t_main:
        if crit.empty:
            st.info("이 이력은 점검 기준 적용 이전 버전으로 분석되었습니다. 다시 분석하면 기준 점검 결과가 표시됩니다.")
        else:
            show_cols = ["이벤트ID", "시간", "채널", "행위", "대상", "수신자", "점검사유"]
            code = crit[crit["구분"] == "기준 점검"]
            hit, miss = code[code["이벤트ID"] != ""], code[code["이벤트ID"] == ""]
            st.markdown(f"##### SA팀 기준 점검 · 해당 {len(hit)}항목 / 전체 {len(code)}항목")
            for r in hit.itertuples():
                ids = [x.strip() for x in r.이벤트ID.split(",")]
                with st.expander(f"⚠️ {r.항목} · {len(ids)}건" + (f" · {r.비고}" if r.비고 else ""), expanded=False):
                    st.caption(r.기준)
                    st.dataframe(tl[tl["이벤트ID"].isin(ids)].reindex(columns=show_cols), hide_index=True, width="stretch")
            if len(miss):
                st.success("해당 없음: " + " · ".join(miss["항목"]))
            with st.expander(f"참고 관찰 {len(ref)}건 (우선 검토 제외, 숨기지 않음)"):
                st.dataframe(crit[crit["구분"] == "참고 관찰"][["항목", "결과"]], hide_index=True, width="stretch")
                if len(ref):
                    st.dataframe(ref.reindex(columns=show_cols), hide_index=True, width="stretch")
            with st.expander("로그 검증 상세", expanded=a["검증상태"] != "정상"):
                st.dataframe(pd.DataFrame(a["검증결과"], columns=["확인 항목", "결과", "내용"]), hide_index=True, width="stretch")
            ai_rows = crit[crit["구분"].str.startswith("AI")]
            st.caption("AI 판단 항목 (LLM 연동 후): " + " · ".join(ai_rows["항목"]) + " · 그 외 기준에 없는 이상징후 포함")

    # ---------------- 타임라인 ----------------
    with t_tl:
        f1, f2, f3 = st.columns([3, 1, 1])
        channels = sorted(tl["채널"].dropna().unique()) if "채널" in tl else []
        sel = f1.multiselect("채널", channels, default=channels, key=f"{key}_ch")
        only_need = f2.toggle("확인 필요만", key=f"{key}_need")
        show_all = f3.toggle("전체 컬럼", key=f"{key}_all")
        view = tl[tl["채널"].isin(sel)] if channels else tl
        if only_need:
            view = view[view["점검결과"].fillna("") == "확인 필요"]
        if not show_all:
            view = view.reindex(columns=[c for c in TIMELINE_BASIC if c in view])
        st.caption(f"{len(view)} / {len(tl)}건")
        st.dataframe(view, hide_index=True, width="stretch", height=430)

    # ---------------- 채널·추이 ----------------
    with t_sum:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### 채널별 건수")
            if len(tl):
                pivot = pd.crosstab(tl["채널"], tl["점검결과"].replace("", "일반"))
                st.bar_chart(pivot, height=260)
        with c2:
            st.markdown("##### 일자별 활동")
            if "날짜" in tl or "시간" in tl:
                daily = tl.assign(날짜=pd.to_datetime(tl["시간"]).dt.date).groupby("날짜").size()
                st.line_chart(daily, height=260)
        st.dataframe(a["채널요약"], hide_index=True, width="stretch")
        st.caption("평소 기간은 약 5개월이라 30일 평균으로 맞춰 최근 30일과 비교합니다. 건수가 적으면 차이가 과장될 수 있습니다.")

    # ---------------- AI 분석 (LLM 연동) ----------------
    with t_llm:
        cfg = _ai_cfg()
        provider_label = st.session_state.get("ai_provider", "Ollama (로컬)")
        result_key = f"ai_result_{analysis_id}"

        h1, h2 = st.columns([5, 1])
        h1.markdown(f"##### AI 분석 &nbsp;&nbsp; **{provider_label}** · `{cfg['model']}`")

        with st.expander("LLM에 전달되는 프롬프트 확인"):
            st.code(ai.build_llm_prompt(a, crit, cands, tl), language="text")

        existing = st.session_state.get(result_key)
        if existing:
            st.markdown(existing)
            if h2.button("다시 요청", key=f"{key}_llm_retry"):
                del st.session_state[result_key]
                st.rerun()
        else:
            if h2.button("분석 요청", key=f"{key}_llm_run", type="primary"):
                try:
                    result = st.write_stream(ai.stream_analyze(cfg, a, crit, cands, tl))
                    st.session_state[result_key] = result
                    st.rerun()
                except (ConnectionError, RuntimeError) as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"AI 연동 오류: {e}")
            else:
                st.info(
                    f"**{provider_label}** / `{cfg['model']}` 에게 이 분석 결과를 전달하여 AI 의견을 받습니다.  \n"
                    "왼쪽 사이드바에서 분석 엔진과 모델을 설정한 뒤 **분석 요청** 버튼을 누르세요."
                )

    # ---------------- 검토 입력 ----------------
    st.divider()
    with st.expander("검토 입력 · 이력 저장", expanded=a["검토상태"] != "검토 완료"):
        with st.form(f"{key}_review"):
            r1, r2, r3, r4 = st.columns(4)
            interview = r1.selectbox("인터뷰 여부", YES_NO, index=YES_NO.index(a["인터뷰여부"]))
            pledge = r2.selectbox("보안서약서 작성 여부", YES_NO, index=YES_NO.index(a["보안서약서"]))
            state = r3.selectbox("검토 상태", REVIEW_STATES, index=REVIEW_STATES.index(a["검토상태"]))
            reviewer = r4.text_input("검토자", value=a["검토자"] or st.session_state.get("user", ""))
            comment = st.text_area("Comment 및 확인 결과", value=a["코멘트"], height=110)
            if st.form_submit_button("검토 내용 저장", type="primary"):
                store.update_review(analysis_id, interview, pledge, comment, state, reviewer)
                st.success("저장했습니다.")
                st.rerun()
        b1, b2 = st.columns([1, 1])
        b1.download_button(
            "이 분석 결과 엑셀로 받기",
            data=to_excel_bytes({"검증결과": pd.DataFrame(a["검증결과"], columns=["확인 항목", "결과", "내용"]),
                                 "기준점검": crit, "통합타임라인": tl, "채널요약": a["채널요약"]}),
            file_name=f"OBI_{a['이름']}_{a['퇴직일자']}_#{a['id']}.xlsx", key=f"{key}_dl")
        with b2.popover("이 이력 삭제"):
            if a["테스트여부"]:
                st.caption("Test 이력은 실제로 지웁니다. 되돌릴 수 없습니다.")
            else:
                st.caption("정식 이력은 목록에서 숨기고 삭제자·일시를 기록합니다 (DB 에는 남음).")
            if st.button("삭제 확인", key=f"{key}_del", type="primary"):
                if a["테스트여부"]:
                    store.delete_analysis(analysis_id)
                else:
                    store.mark_deleted(analysis_id, st.session_state.get("user", ""))
                if st.session_state.get("current_id") == analysis_id:
                    st.session_state.pop("current_id")
                st.rerun()


# ---------------------------------------------------------------------------
# 페이지
# ---------------------------------------------------------------------------
def page_new():
    top_bar("새 분석")
    has_result = bool(st.session_state.get("current_id"))
    with st.expander("새 분석 실행" if has_result else "분석할 로그 올리기", expanded=not has_result):
        with st.form("new_analysis"):
            c1, c2, c3, c4 = st.columns(4)
            company = c1.selectbox("회사 *", COMPANIES)
            name = c2.text_input("이름 *")
            rank = c3.text_input("직급")
            dept = c4.text_input("부서")
            d1, d2, d3 = st.columns([1, 1, 2])
            retire = d1.date_input("퇴직(예정)일 *", value=date.today())
            base = d2.date_input("로그 추출일", value=None, help="비워두면 로그의 마지막 날짜를 기준일로 사용합니다")
            uploaded = d3.file_uploader("추출로그 by DDI *", type=["xlsx", "xls"])
            submitted = st.form_submit_button("분석 실행", type="primary")

    if submitted:
        if not uploaded or not name:
            st.error("이름과 추출로그 파일은 필수입니다.")
        else:
            info = dict(회사=company, 이름=name.strip(), 직급=rank, 부서=dept, 퇴직일자=str(retire),
                        원본파일명=uploaded.name, 분석자=st.session_state.get("user", ""))
            with st.spinner("로그를 정리하고 있습니다. 암호화 파일은 엑셀을 여는 데 시간이 조금 걸립니다."):
                try:
                    st.session_state["current_id"] = run_analysis(uploaded, info, str(base) if base else None)
                except SystemExit as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"분석 중 오류가 발생했습니다: {e}")
                    st.exception(e)

    if st.session_state.get("current_id"):
        render_detail(st.session_state["current_id"], key="new")


def page_history():
    top_bar("이력 조회")
    df = store.list_analyses()
    if df.empty:
        st.info("아직 저장된 이력이 없습니다.")
        return
    f1, f2, f3 = st.columns([2, 1, 2])
    q = f1.text_input("이름·부서 검색")
    comps = f2.multiselect("회사", COMPANIES, default=COMPANIES)
    states = f3.multiselect("검토 상태", REVIEW_STATES, default=REVIEW_STATES)
    view = df[df["검토상태"].isin(states) & df["회사"].isin(comps)]
    if q:
        view = view[view["이름"].str.contains(q, na=False) | view["부서"].str.contains(q, na=False)]

    cols = ["id", "분석일시", "회사", "이름", "직급", "부서", "퇴직일자", "인터뷰여부", "보안서약서",
            "코멘트", "검토상태", "검토자", "검증상태"]
    st.dataframe(view[cols].rename(columns={"id": "이력#", "인터뷰여부": "인터뷰 여부", "보안서약서": "보안서약서 작성 여부",
                                            "코멘트": "Comment 및 확인 결과"}),
                 hide_index=True, width="stretch")
    st.download_button("이력 엑셀 내보내기 (기존 양식)",
                       data=to_excel_bytes({"이력관리": store.to_legacy_frame(view)}),
                       file_name=f"OBI_이력관리_{date.today()}.xlsx")

    st.divider()
    options = {f"#{r.id} {r.이름} ({r.회사} {r.부서}) · 퇴직 {r.퇴직일자}": r.id for r in view.itertuples()}
    if options:
        pick = st.selectbox("상세 보기", list(options))
        render_detail(options[pick], key=f"hist_{options[pick]}")


with st.sidebar:
    st.markdown('<div class="obi-side"><div class="l">OBI</div>'
                '<div class="s">OFFBOARDING INSPECTOR</div></div>', unsafe_allow_html=True)
    menu = st.radio("메뉴", ["새 분석", "이력 조회"], label_visibility="collapsed")
    st.session_state["user"] = st.text_input("사용자 이름", value=st.session_state.get("user", ""))
    st.divider()
    st.markdown("**AI 분석 설정**")
    ai_provider = st.selectbox(
        "분석 엔진", ["Ollama (로컬)", "OpenAI 호환", "Anthropic"],
        key="ai_provider",
    )
    if ai_provider == "Ollama (로컬)":
        st.text_input("서버 URL", key="ollama_base_url",
                      value=st.session_state.get("ollama_base_url", ai.PROVIDER_DEFAULTS["ollama"]["base_url"]))
        st.text_input("모델명", key="ollama_model",
                      value=st.session_state.get("ollama_model", ai.PROVIDER_DEFAULTS["ollama"]["model"]))
    elif ai_provider == "OpenAI 호환":
        st.text_input("API 엔드포인트", key="openai_base_url",
                      value=st.session_state.get("openai_base_url", ai.PROVIDER_DEFAULTS["openai"]["base_url"]))
        st.text_input("API 키", key="openai_api_key", type="password")
        st.text_input("모델명", key="openai_model",
                      value=st.session_state.get("openai_model", ai.PROVIDER_DEFAULTS["openai"]["model"]))
    else:  # Anthropic
        st.text_input("API 키", key="anthropic_api_key", type="password")
        st.text_input("모델명", key="anthropic_model",
                      value=st.session_state.get("anthropic_model", ai.PROVIDER_DEFAULTS["anthropic"]["model"]))
    st.divider()
    st.caption(f"이력 저장 위치\n\n`{store.DB_PATH}`")

if menu == "새 분석":
    page_new()
else:
    page_history()
