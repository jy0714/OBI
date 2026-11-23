"""
OBI (Offboarding Inspector) 화면 - 1차 버전 (AI 분석 연동 전)

실행: streamlit run app.py
"""
import io
import os
import tempfile
import time
from datetime import date

import pandas as pd
import streamlit as st

import obi_ai as ai
import obi_charts as charts
import obi_config as config
import obi_pipeline as pipeline
import obi_report as report
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


def read_upload(uploaded):
    """업로드 파일을 임시 저장 → 한 번 읽기(LogWorkbook) → 임시 파일 삭제 (REQ-10).
    읽은 결과는 session_state 에 두고 분석 실행 때 다시 쓴다 (암호화 파일은 엑셀을 다시 열지 않음)."""
    suffix = os.path.splitext(uploaded.name)[1] or ".xlsx"
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input" + suffix)
        with open(src, "wb") as f:
            f.write(uploaded.getbuffer())
        return pipeline.read_log(src)


def run_analysis(xls, info, base_date, test=False, sources=None):
    """읽어 둔 로그 → 전처리 → 이력 자동 저장"""
    with tempfile.TemporaryDirectory() as tmp:
        r = pipeline.analyze_file(xls, os.path.join(tmp, "result.xlsx"), info, base_date)
    return pipeline.save(r, test=test, sources=sources)


# ---------------------------------------------------------------------------
# AI 프로바이더 설정 읽기
# ---------------------------------------------------------------------------
OLLAMA_LABEL, OPENAI_LABEL = "Ollama (로컬)", "OpenAI 형식 API"


def _ai_cfg():
    """사이드바 선택 + obi.ini 값 → stream_analyze 용 cfg. 고를 모델이 없으면 model = "" """
    conf = st.session_state.get("obi_conf") or config.load()
    if st.session_state.get("ai_provider", OLLAMA_LABEL) == OPENAI_LABEL:
        return {
            "provider": "openai",
            "model":    (st.session_state.get("openai_model") or conf["openai_model"]).strip(),
            "base_url": (st.session_state.get("openai_base_url") or conf["openai_base_url"]).strip(),
            "api_key":  (st.session_state.get("openai_api_key") or conf["openai_api_key"]).strip(),
        }
    return {
        "provider": "ollama",
        "model":    st.session_state.get("ollama_model") or "",
        "base_url": conf["ollama_base_url"],
        "api_key":  "",
    }


# ---------------------------------------------------------------------------
# 분석 상세 (새 분석 직후와 이력 조회에서 공통 사용)
# ---------------------------------------------------------------------------
# 분석가에게 보여 줄 원본 로그 컬럼 (REQ-17). 이벤트ID 는 데이터에만 두고 화면에서는 숨긴다.
# 원본시트·원본행은 DDI 원본 엑셀에서 그 행을 찾는 근거라 남긴다. 통합타임라인 탭은 이벤트ID 유지.
RAW_VIEW = ["시간", "채널", "행위", "대상", "수신자", "파일목록", "점검사유", "원본시트", "원본행"]


def raw_log(df):
    """원본 로그 표 (이벤트ID 숨김)"""
    st.dataframe(df.reindex(columns=RAW_VIEW), hide_index=True, width="stretch")


def selectable_table(df, tl, key, ids_col, view, caption="행을 누르면 원본 로그가 아래에 나옵니다.", height=None):
    """행을 누르면 그 행의 근거 이벤트 원본 로그를 보여 주는 표 (REQ-03·05·09).
    view: 화면에 보일 컬럼. ids_col(이벤트ID)은 숨겨도 df 에 있으므로 원본 로그 연결은 그대로 된다 (REQ-17)."""
    opts = dict(hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=key,
                column_order=[c for c in view if c in df.columns])
    if height:
        opts["height"] = height
    event = st.dataframe(df, **opts)
    rows = event.selection.rows if event else []
    if rows:
        events = pipeline.events_of(tl, df.iloc[rows[0]][ids_col])
        st.markdown(f"**원본 로그** · {len(events)}건")
        raw_log(events)
    elif caption:
        st.caption(caption)


def _picked_bar(event):
    """일자별 활동 막대 클릭 → (날짜, 채널) 또는 None (REQ-16)"""
    try:
        pts = event["selection"][charts.PICK]
    except (KeyError, TypeError):
        return None
    if isinstance(pts, dict):        # 버전에 따라 {"일자": [...], "채널": [...]} 형태
        pts = [dict(zip(pts, vals)) for vals in zip(*pts.values())] if pts else []
    p = pts[0] if pts else {}
    day, ch = p.get("일자"), p.get("채널")
    if isinstance(day, list):
        day = day[0] if day else None
    if isinstance(ch, list):
        ch = ch[0] if ch else None
    return (str(day), str(ch)) if day and ch else None


def render_dashboard(tl, a, key):
    """REQ-02 채널·추이 대시보드 (집계는 pipeline.dashboard, 차트는 obi_charts)
    REQ-16: 항목(채널 + 확인 필요만)을 고르면 일자별 활동·전송 용량·이벤트 표가 함께 바뀐다."""
    full = pipeline.dashboard(tl, a["채널요약"], a["기준일"], a["퇴직일자"])
    if full["daily"].empty:
        st.info("시간이 기록된 이벤트가 없어 표시할 추이가 없습니다.")
        return

    st.markdown("##### 일자별 활동")
    present = set(tl["채널"].dropna())
    channels = [c for c in charts.CHANNEL_ORDER if c in present] + sorted(present - set(charts.CHANNEL_ORDER))
    f1, f2 = st.columns([4, 1])
    sel = f1.multiselect("항목", channels, default=channels, key=f"{key}_dash_ch",
                         help="고른 채널만 차트와 아래 표에 나옵니다. 모두 지우면 전체 채널입니다.")
    need_only = f2.toggle("확인 필요만", key=f"{key}_dash_need")
    picked_ch = None if set(sel) in (set(), set(channels)) else sel        # None = 전체 채널
    filtered = picked_ch is not None or need_only
    d = pipeline.dashboard(tl, a["채널요약"], a["기준일"], a["퇴직일자"], picked_ch, need_only) if filtered else full

    bar = None
    if d["daily"].empty:
        st.info("고른 항목의 이벤트가 없습니다. 항목을 바꿔 보세요.")
    else:
        ev = st.altair_chart(charts.daily_activity(d, selectable=True), width="stretch",
                             on_select="rerun", selection_mode=charts.PICK, key=f"{key}_dash_chart")
        bar = _picked_bar(ev)
        st.caption("막대: 채널별 건수 (누르면 그 날짜·채널의 이벤트, 두 번 누르면 해제) · 빨간 점: 그날의 확인 필요 건수 · 점선: "
                   + " / ".join(f"{r.이름} {r.날짜}" for r in d["markers"].itertuples()))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("###### 평소 대비 최근 (30일 환산 건수)")
        if full["compare"].empty:
            st.caption("채널 요약이 없습니다.")
        else:
            st.altair_chart(charts.recent_vs_usual(full), width="stretch")
    with c2:
        st.markdown("###### 업무시간 외 비중 (%)")
        st.altair_chart(charts.off_hours_share(full), width="stretch")
    with c3:
        st.markdown("###### 일자별 전송 용량 (MB)" + (" · 고른 항목" if filtered else ""))
        if d["bytes"].empty:
            st.caption("전송 용량 기록이 없습니다.")
        else:
            st.altair_chart(charts.daily_bytes(d), width="stretch")

    # 막대 클릭 > 날짜 선택 > 항목만 고름 순서로 아래 표를 정한다 (REQ-09·16)
    counts = d["daily"].groupby("날짜")["건수"].sum()
    needs = d["need"].set_index("날짜")["확인필요"]
    day = st.selectbox("날짜를 고르면 그날의 이벤트가 나옵니다", [None] + d["dates"], key=f"{key}_day",
                       format_func=lambda x: "날짜 선택" if x is None else
                       f"{x} · {counts.get(x, 0)}건" + (f" (확인 필요 {needs.get(x, 0)})" if needs.get(x, 0) else ""))
    if bar:
        events, title = pipeline.events_on(tl, bar[0], [bar[1]], need_only), f"{bar[0]} · {bar[1]}"
    elif day:
        events, title = pipeline.events_on(tl, day, picked_ch, need_only), day
    elif filtered:
        events, title = pipeline.events_on(tl, None, picked_ch, need_only), "전체 기간"
    else:
        events = None
    if events is not None:
        what = [", ".join(picked_ch)] if picked_ch and not bar else []
        what += ["확인 필요"] if need_only else []
        st.markdown(f"**{title}** · " + " ".join(what + [f"이벤트 {len(events)}건"]))
        if events.empty:
            st.caption("해당하는 이벤트가 없습니다.")
        else:
            raw_log(events)
    with st.expander("채널 요약 표"):
        st.dataframe(a["채널요약"], hide_index=True, width="stretch")
        st.caption("평소 기간은 약 5개월이라 30일 평균으로 맞춰 최근 30일과 비교합니다. 건수가 적으면 차이가 과장될 수 있습니다.")


def render_pledge(a, key):
    """REQ-12 보안서약서 PDF: 현재 파일 정보·내려받기·올리기(교체)"""
    aid, cur = a["id"], a["서약서"]
    st.markdown("**보안서약서 PDF**" + (" · 인터뷰 진행 → 필수" if a["인터뷰여부"] == "예" and not a["테스트여부"] else ""))
    c1, c2 = st.columns([3, 2])
    with c1:
        if cur:
            st.caption(f"📄 {cur['원본파일명']} · {cur['크기'] / 1024:,.0f}KB · 올린 사람 {cur['올린사람'] or '-'}"
                       f" · {cur['올린일시']}" + (f" · 이전 {a['서약서수'] - 1}개 보관" if a["서약서수"] > 1 else ""))
            dl_key = f"pledge_dl_{cur['id']}"
            if st.button("서약서 내려받기", key=f"{key}_pledge_read"):     # 버튼을 눌렀을 때만 파일을 읽는다
                try:
                    st.session_state[dl_key] = store.read_pledge(cur["id"], st.session_state.get("user", ""))
                except (OSError, LookupError, ValueError) as e:
                    st.error(str(e))
            if dl_key in st.session_state:
                st.download_button(f"⬇ {cur['원본파일명']}", st.session_state[dl_key], file_name=cur["원본파일명"],
                                   mime="application/pdf", key=f"{key}_pledge_dl")
        else:
            st.caption("올린 서약서가 없습니다.")
    with c2:
        up = st.file_uploader("교체할 PDF 올리기" if cur else "PDF 올리기", type=["pdf"], key=f"{key}_pledge_up",
                              help=f"PDF 만, 최대 {store.PLEDGE_MAX_MB}MB. 다시 올리면 이전 파일은 보관되고 새 파일이 현재본이 됩니다.")
        done_key = f"{key}_pledge_done"
        if up is not None and st.session_state.get(done_key) != up.file_id:   # 같은 업로드를 두 번 저장하지 않음
            try:
                store.save_pledge(aid, up.name, up.getvalue(), st.session_state.get("user", ""))
                st.session_state[done_key] = up.file_id
                st.rerun()
            except ValueError as e:
                st.session_state[done_key] = up.file_id
                st.error(str(e))


def candidate_card(r, tl, key):
    with st.container(border=True):
        st.markdown(chip(f"{GRADE_ICON.get(r.등급, '')} {r.등급}", r.등급) +
                    f'<span class="obi-card-title">{r.유형}</span>'
                    f'<div class="obi-card-meta">{r.기간} · {r.건수}건</div>',
                    unsafe_allow_html=True)
        st.write(r.설명)
        with st.expander("판단 근거와 원본 로그"):
            st.dataframe(pd.DataFrame(
                [{"판단 요소": k, "주체": ai.WEIGHTS[k][0], "판단": v, "근거": r.근거[k]}
                 for k, v in r.판단.items()]),
                hide_index=True, width="stretch")
            ids = [x.strip() for x in r.근거이벤트.split(",")]
            raw_log(pipeline.events_of(tl, ids))


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
                f" · 계정 {a['계정']} · 로그 검증 {a['검증상태']}"
                + (" · 인적 사항 " + " ".join(f"{k}({v})" for k, v in src.items())
                   if (src := {k: v for k, v in (a.get("인적사항출처") or {}).items()
                               if k in pipeline.PERSON_FIELDS}) else "")
                + "</div>", unsafe_allow_html=True)
    h2.markdown(chip(f"{GRADE_ICON.get(top_grade, '')} 최고 등급 {top_grade}", top_grade)
                + (chip("TEST", "Low") if a["테스트여부"] else ""), unsafe_allow_html=True)
    missing = pipeline.review_missing(a)
    if missing:
        st.warning("**입력 필요** · 정식 이력은 검토 입력을 마쳐야 합니다 (리포트 생성 불가): " + ", ".join(missing))

    pmail = pipeline.personal_mail(tl)
    m = st.columns(5)
    # 검토는 사건(검토 후보) 단위 — 행위 건수는 보조 설명으로만 (REQ-15)
    m[0].metric("검토 후보", f"{len(cands)}건", help=f"확인 필요 행위 {len(need)}건을 사건 단위로 묶음")
    m[1].metric("최고 등급", top_grade)
    m[2].metric("개인메일 발송", f"{len(pmail)}건 (첨부 {int((pmail['첨부 수'] > 0).sum())}건)")
    m[3].metric("참고 관찰", f"{len(ref)}건")
    m[4].metric("전체 이벤트", f"{a['이벤트수']}건")

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
            st.caption("검토 후보에 등장한 파일이 없습니다.")
        else:
            selectable_table(files, tl, f"{key}_files", "근거 이벤트ID", pipeline.KEY_FILE_VIEW)

    # ---------------- 개인메일 발송 (REQ-05) ----------------
    with st.container(border=True):
        n_att = int((pmail["첨부 수"] > 0).sum())
        st.markdown(f"##### 개인메일 발송 · {len(pmail)}건 &nbsp; :red-badge[첨부 {n_att}건]")
        if pmail.empty:
            st.caption("개인메일 도메인으로 보낸 메일이나 개인메일 사이트 업로드가 없습니다.")
        else:
            view = pmail.assign(첨부=pmail["첨부"].map(lambda x: "📎 첨부" if x else ""))
            selectable_table(view, tl, f"{key}_pmail", "이벤트ID", pipeline.PERSONAL_MAIL_VIEW,
                             caption="📎 첨부가 있는 건이 위에 옵니다. 참고(차단·사내 동반·개인 문서)로 분류된 건도 함께 표시합니다. "
                                     "행을 누르면 원본 로그가 나옵니다.")

    t_ai, t_main, t_tl, t_sum, t_llm = st.tabs(
        [f"검토 후보 ({len(cands)})", "기준 점검", "통합타임라인", "채널·추이", "AI 분석"])

    # ---------------- 검토 후보 ----------------
    with t_ai:
        if cands.empty:
            st.info("확인이 필요한 행위가 없어 검토 후보가 없습니다.")
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
                    st.dataframe(pipeline.events_of(tl, ids).reindex(columns=show_cols), hide_index=True, width="stretch")
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
        render_dashboard(tl, a, key)

    # ---------------- AI 분석 (LLM 연동) ----------------
    with t_llm:
        # AI 의견은 DB 에 저장 → 브라우저를 새로 열어도 보인다 (REQ-07)
        cfg = _ai_cfg()
        provider_label = st.session_state.get("ai_provider", OLLAMA_LABEL)

        h1, h2 = st.columns([5, 1])
        h1.markdown(f"##### AI 분석 &nbsp;&nbsp; **{provider_label}** · `{cfg['model'] or '모델 없음'}`")

        if cfg["provider"] != "ollama":       # REQ-13 외부 프로바이더로 나가는 개인정보 경고
            st.warning("외부 API 사용 중 · 수신자 주소와 파일명이 외부로 전송됩니다. "
                       "전송 내용은 아래 '프롬프트 확인'에서 볼 수 있습니다.", icon="⚠️")

        with st.expander("LLM에 전달되는 프롬프트 확인"):
            st.code(ai.build_llm_prompt(a, crit, cands, tl), language="text")

        existing = a["AI의견"]
        clicked = h2.button("다시 요청" if existing else "분석 요청", key=f"{key}_llm_run",
                            type="secondary" if existing else "primary", disabled=not cfg["model"])
        if clicked:
            try:
                result = st.write_stream(ai.stream_analyze(cfg, a, crit, cands, tl))
                store.save_ai_opinion(analysis_id, result, cfg["provider"], cfg["model"],
                                      st.session_state.get("user", ""))
                st.rerun()
            except (ConnectionError, RuntimeError) as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"AI 연동 오류: {e}")
        elif existing:
            st.caption(f"{a['AI프로바이더']} · `{a['AI모델']}` · 생성 {a['AI생성일시']} · 요청 {a['AI요청자'] or '-'}"
                       + (f" · 이전 생성: {', '.join(a['AI이전생성일시'])}" if a["AI이전생성일시"] else ""))
            st.markdown(existing)
        if not cfg["model"]:
            st.warning("선택할 수 있는 AI 모델이 없습니다. 왼쪽 사이드바의 AI 분석 설정을 확인하세요.")
        elif not existing:
            st.info(
                f"**{provider_label}** / `{cfg['model']}` 에게 이 분석 결과를 전달하여 AI 의견을 받습니다.  \n"
                "왼쪽 사이드바에서 분석 엔진과 모델을 설정한 뒤 **분석 요청** 버튼을 누르세요. 받은 의견은 이력에 저장됩니다."
            )

    # ---------------- 검토 입력 ----------------
    st.divider()
    with st.expander("검토 입력 · 이력 저장" + (" (Test — 선택 입력)" if a["테스트여부"] else " (필수)"),
                     expanded=bool(missing) or a["검토상태"] != "검토 완료"):
        render_pledge(a, key)
        has_file = a["서약서"] is not None
        with st.form(f"{key}_review"):
            r1, r2, r3, r4 = st.columns(4)
            interview = r1.selectbox("인터뷰 여부", YES_NO, index=YES_NO.index(a["인터뷰여부"]),
                                     help="'예'이면 보안서약서 PDF 를 올려야 검토를 완료할 수 있습니다.")
            # '예'는 서약서 파일이 있을 때만 고를 수 있다 (파일을 올리면 자동으로 '예')
            pledge_opts = YES_NO if has_file else [x for x in YES_NO if x != "예"]
            pledge = r2.selectbox("보안서약서 작성 여부", pledge_opts,
                                  index=pledge_opts.index(a["보안서약서"]) if a["보안서약서"] in pledge_opts else 0,
                                  help=None if has_file else "'예'는 서약서 PDF 를 올리면 자동으로 선택됩니다.")
            state = r3.selectbox("검토 상태", REVIEW_STATES, index=REVIEW_STATES.index(a["검토상태"]))
            reviewer = r4.text_input("검토자", value=a["검토자"] or st.session_state.get("user", ""))
            comment = st.text_area("Comment 및 확인 결과", value=a["코멘트"], height=110)
            if st.form_submit_button("검토 내용 저장", type="primary"):
                lack = pipeline.review_missing(dict(테스트여부=a["테스트여부"], 인터뷰여부=interview, 보안서약서=pledge,
                                                    검토자=reviewer, 코멘트=comment, 서약서수=a["서약서수"]))
                if lack:
                    st.error("다음 항목을 입력해야 저장할 수 있습니다: " + ", ".join(lack))
                else:
                    store.update_review(analysis_id, interview, pledge, comment, state, reviewer)
                    st.success("저장했습니다.")
                    st.rerun()
        # 리포트: 버튼을 눌렀을 때만 만든다 (REQ-07). 정식 이력은 검토 입력을 마쳐야 한다 (REQ-08)
        b1, b2 = st.columns([1, 1])
        report_key = f"report_{analysis_id}"
        if b1.button("리포트 생성 (엑셀)", key=f"{key}_report", disabled=bool(missing),
                     help="검토 입력을 마친 뒤 생성할 수 있습니다: " + ", ".join(missing) if missing else None):
            who = st.session_state.get("user", "")
            name = report.file_name(a)
            st.session_state[report_key] = (name, report.to_xlsx(report.build_report(a, who)))
            store.log_report(analysis_id, who, "xlsx", name)
        if report_key in st.session_state:
            name, data = st.session_state[report_key]
            b1.download_button(f"⬇ {name}", data=data, file_name=name, key=f"{key}_dl")
        reports = store.list_reports(analysis_id)
        if len(reports):
            b1.caption("리포트 생성 이력: " + " · ".join(f"{r.생성일시} {r.생성자}" for r in reports.head(3).itertuples())
                       + (f" 외 {len(reports) - 3}건" if len(reports) > 3 else ""))
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
                for k in ("current_id",):
                    if st.session_state.get(k) == analysis_id:
                        st.session_state.pop(k)
                st.session_state.pop("board_table", None)
                st.rerun()


# ---------------------------------------------------------------------------
# 페이지
# ---------------------------------------------------------------------------
def page_new():
    top_bar("새 분석")
    has_result = bool(st.session_state.get("current_id"))
    with st.expander("새 분석 실행" if has_result else "분석할 로그 올리기", expanded=not has_result):
        # 1) 파일을 먼저 올린다 — 폼 밖이라 올리는 즉시 읽어서 인적 사항을 채운다 (REQ-10)
        uploaded = st.file_uploader("추출로그 by DDI *", type=["xlsx", "xls"], key="upload_file")
        up = st.session_state.get("upload")
        if uploaded is None:
            st.session_state.pop("upload", None)
            up = None
        elif up is None or up["id"] != uploaded.file_id:
            with st.spinner("로그를 읽고 있습니다. 암호화 파일은 엑셀을 여는 데 시간이 조금 걸립니다."):
                try:
                    xls, person = read_upload(uploaded)
                    up = dict(id=uploaded.file_id, name=uploaded.name, xls=xls, person=person)
                    st.session_state["upload"] = up
                    for k, field in (("f_name", "이름"), ("f_dept", "부서")):
                        st.session_state[k] = person.get(field, "")   # 위젯 생성 전에 값 채우기
                except SystemExit as e:
                    st.error(str(e))
                    up = None
        person = up["person"] if up else {}
        if up:
            got = [f"{k} {person[k]}" for k in pipeline.PERSON_FIELDS if person.get(k)]
            st.caption(("로그에서 채운 값: " + ", ".join(got) + " · 고칠 수 있습니다" if got
                        else "로그에서 인적 사항을 찾지 못했습니다. 직접 입력하세요.")
                       + (f" · ⚠️ 로그 값이 여러 개: {', '.join(g)}"
                          if (g := [k for k in person.get("갈림", []) if k in pipeline.PERSON_FIELDS]) else ""))

        # 2) 인적 사항·조건 입력
        with st.form("new_analysis"):
            c1, c2, c3 = st.columns(3)
            company = c1.selectbox("회사 *", COMPANIES)
            name = c2.text_input("이름 *", key="f_name")
            dept = c3.text_input("부서", key="f_dept")
            d1, d2, _ = st.columns([1, 1, 2])
            retire = d1.date_input("퇴직(예정)일 *", value=date.today())
            base = d2.date_input("로그 추출일", value=None, help="비워두면 로그의 마지막 날짜를 기준일로 사용합니다")
            s1, s2 = st.columns([1, 4])
            submitted = s1.form_submit_button("분석 실행", type="primary")
            test = s2.checkbox("Test로 실행", help="검토 입력을 요구하지 않고, 게시판 기본 목록과 기존 양식 내보내기에서 빠집니다. "
                                                  "Test 이력은 실제 삭제할 수 있습니다.")

    if submitted:
        if not up or not name.strip():
            st.error("이름과 추출로그 파일은 필수입니다.")
        else:
            # 직급은 화면에 없다 (REQ-14). 로그에서 뽑힌 값만 저장해 기존 양식 내보내기에 쓴다
            info = dict(회사=company, 이름=name.strip(), 직급=str(person.get("직급") or "").strip(),
                        부서=dept.strip(), 퇴직일자=str(retire),
                        원본파일명=up["name"], 분석자=st.session_state.get("user", ""))
            sources = pipeline.person_sources(person, info)
            with st.spinner("로그를 정리하고 있습니다."):
                try:
                    st.session_state["current_id"] = run_analysis(up["xls"], info, str(base) if base else None,
                                                                  test, sources)
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

    with st.container(border=True):
        f1, f2, f3, f4 = st.columns([2, 1.2, 1.6, 1.6])
        q = f1.text_input("이름·부서 검색")
        comps = f2.multiselect("회사", COMPANIES, default=COMPANIES)
        states = f3.multiselect("검토 상태", REVIEW_STATES, default=REVIEW_STATES)
        grade_opts = list(ai.GRADES) + [pipeline.NO_GRADE]
        grades = f4.multiselect("최고 등급", grade_opts, default=grade_opts)
        g1, g2, g3 = st.columns([1, 1, 2.6])
        d_from = g1.date_input("분석일 부터", value=None)
        d_to = g2.date_input("분석일 까지", value=None)
        include_test = g3.toggle("Test 이력 포함", value=False)
    view = pipeline.board(df, q, comps, states, grades, d_from, d_to, include_test)

    st.caption(f"{len(view)}건 · 최신순 · 행을 누르면 아래에 상세가 나옵니다 · 저장 위치 `{store.DB_PATH}`")
    event = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key="board_table")
    picked_ids = view["이력#"].tolist()
    st.download_button("이력 엑셀 내보내기 (기존 양식 · Test 제외)",
                       data=to_excel_bytes({"이력관리": store.to_legacy_frame(df[df["id"].isin(picked_ids)])}),
                       file_name=f"OBI_이력관리_{date.today()}.xlsx")

    # 고른 행의 상세를 게시판 아래에 (REQ-09). 필터로 표가 줄어 선택 위치가 사라지면 상세를 닫는다
    rows = event.selection.rows if event else []
    if rows and rows[0] < len(view):
        pick = int(view.iloc[rows[0]]["이력#"])
        st.divider()
        render_detail(pick, key=f"hist_{pick}")
    else:
        st.caption("게시판의 행을 누르면 이 아래에 분석 상세가 열립니다.")


# ---------------------------------------------------------------------------
# 접속 인증 — obi.ini [auth] password (4자리 숫자) 를 맞혀야 메인 화면이 열린다
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILS, LOGIN_LOCK_SECONDS = 5, 30


def login_gate():
    conf = config.load()
    st.session_state["obi_conf"] = conf
    if st.session_state.get("obi_authed") and not conf["오류"]:
        return
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown('<div class="obi-side" style="padding-top:12vh"><div class="l">OBI</div>'
                    '<div class="s">OFFBOARDING INSPECTOR</div></div>', unsafe_allow_html=True)
        if conf["오류"]:
            st.error(conf["오류"])
            st.stop()
        locked = st.session_state.get("login_locked_until", 0) - time.time()
        with st.form("login"):
            pw = st.text_input("비밀번호 (숫자 4자리)", type="password", max_chars=4, key="login_pw")
            ok = st.form_submit_button("확인", type="primary", width="stretch")
        if locked > 0:
            st.error(f"비밀번호를 {LOGIN_MAX_FAILS}번 틀렸습니다. {int(locked) + 1}초 뒤에 다시 시도하세요.")
        elif ok:
            if config.check_password(conf, pw):
                st.session_state["obi_authed"] = True
                st.session_state.pop("login_fails", None)
                st.rerun()
            fails = st.session_state.get("login_fails", 0) + 1
            st.session_state["login_fails"] = fails
            if fails >= LOGIN_MAX_FAILS:
                st.session_state["login_fails"] = 0
                st.session_state["login_locked_until"] = time.time() + LOGIN_LOCK_SECONDS
            st.error("비밀번호가 맞지 않습니다.")
    st.stop()


login_gate()

with st.sidebar:
    st.markdown('<div class="obi-side"><div class="l">OBI</div>'
                '<div class="s">OFFBOARDING INSPECTOR</div></div>', unsafe_allow_html=True)
    menu = st.radio("메뉴", ["새 분석", "이력 조회"], label_visibility="collapsed")
    st.session_state["user"] = st.text_input("사용자 이름", value=st.session_state.get("user", ""))
    st.divider()
    st.markdown("**AI 분석 설정**")
    conf = st.session_state["obi_conf"]
    ai_provider = st.selectbox("분석 엔진", [OLLAMA_LABEL, OPENAI_LABEL], key="ai_provider")
    if ai_provider == OLLAMA_LABEL:
        try:
            models = ai.list_ollama_models(conf["ollama_base_url"], conf["ollama_models"])
        except ConnectionError as e:
            models = []
            st.warning(str(e))
        else:
            if not models:
                want = ", ".join(conf["ollama_models"]) or "(전체)"
                st.warning(f"`ollama list` 에 사용할 수 있는 모델이 없습니다. 허용 모델: {want}  \n"
                           f"`ollama pull <모델명>` 으로 받은 뒤 새로고침하세요.")
        if models:
            st.selectbox("모델", models, key="ollama_model", help="`ollama list` 에 있는 모델 중 obi.ini 에서 허용한 것만 보입니다.")
        else:
            st.session_state.pop("ollama_model", None)
        st.button("모델 목록 새로고침", key="ollama_refresh")
    else:
        st.text_input("API 엔드포인트", key="openai_base_url",
                      value=st.session_state.get("openai_base_url", conf["openai_base_url"]))
        st.text_input("API 키", key="openai_api_key", type="password",
                      placeholder="obi.ini 의 키 사용" if conf["openai_api_key"] else "")
        st.text_input("모델명", key="openai_model",
                      value=st.session_state.get("openai_model", conf["openai_model"]))
    st.divider()
    if st.button("잠금 (로그아웃)", key="logout"):
        st.session_state.pop("obi_authed", None)
        st.rerun()
    st.caption(f"이력 저장 위치\n\n`{store.DB_PATH}`\n\n서약서 저장 위치\n\n`{store.files_dir()}`")

if menu == "새 분석":
    page_new()
else:
    page_history()
