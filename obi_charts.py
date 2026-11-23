"""
OBI 대시보드 차트 (Altair) — 화면 없이 만들 수 있게 분리 (REQ-02)

입력은 obi_pipeline.dashboard() 의 집계 dict. app.py 는 여기서 받은 차트를 그리기만 한다.
색 규칙: 채널은 고정 순서(필터로 채널이 빠져도 색이 바뀌지 않음), 확인 필요는 상태색(critical).
팔레트 검증: dataviz validate_palette.js --mode light → PASS (대비 3:1 미만 3색은 범례·툴팁·표로 보완)
"""
import altair as alt

CHANNEL_ORDER = ["웹", "웹 업로드", "이메일", "USB", "USB(DLP)", "출력"]
CHANNEL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
NEED_COLOR = "#d03b3b"      # 상태색 critical — 채널 색과 겹치지 않음
MARK_COLOR = "#5A6475"      # 기준선·보조 글자
BASE_GRAY = "#B8C0CC"       # 비교 기준(평소)


def _channel_color():
    return alt.Color("채널:N", scale=alt.Scale(domain=CHANNEL_ORDER, range=CHANNEL_COLORS),
                     legend=alt.Legend(orient="top", title=None))


def _date_x():
    return alt.X("yearmonthdate(날짜):T", title=None, axis=alt.Axis(format="%m-%d", labelAngle=0, grid=False))


PICK = "pick"      # 일자별 활동 막대 선택 이름 (REQ-16). 선택값은 {"일자": "YYYY-MM-DD", "채널": ...}


def daily_activity(d, height=300, selectable=False):
    """일자별 활동: 채널별 누적 막대 + 확인 필요 점 + 최근 시작·기준일·퇴직일 세로선 (한 축: 건수)
    확인 필요는 선으로 잇지 않는다 — 활동 없는 날을 건너뛰어 이으면 그 사이에도 행위가 있었던 것처럼 보인다.
    selectable: 막대를 누르면 그 날짜·채널을 고른다 (REQ-16). '일자'는 날짜를 문자열 그대로 돌려받기 위한 사본
    (시간축에 쓰는 '날짜'는 Vega-Lite 가 날짜값으로 바꿔 버린다)."""
    daily = d["daily"].groupby(["날짜", "채널"], as_index=False)["건수"].sum()
    daily["일자"] = daily["날짜"]
    # 6개월 시간축에서는 막대 폭이 몇 px 라 테두리(간격)를 넣으면 막대가 지워진다
    bars = alt.Chart(daily).mark_bar(cornerRadiusTopLeft=1, cornerRadiusTopRight=1).encode(
        x=_date_x(), y=alt.Y("건수:Q", stack="zero", title="건수", axis=alt.Axis(gridOpacity=0.4)),
        color=_channel_color(), order=alt.Order("채널:N"),
        tooltip=[alt.Tooltip("일자:N", title="날짜"), "채널:N", alt.Tooltip("건수:Q", title="건수")])
    if selectable:
        pick = alt.selection_point(name=PICK, fields=["일자", "채널"], on="click", clear="dblclick")
        bars = bars.add_params(pick).encode(opacity=alt.condition(pick, alt.value(1), alt.value(0.35)))
    need = alt.Chart(d["need"][d["need"]["확인필요"] > 0]).mark_point(
        color=NEED_COLOR, size=70, filled=True, opacity=1, stroke="white", strokeWidth=2).encode(
        x=_date_x(), y="확인필요:Q",
        tooltip=[alt.Tooltip("날짜:N"), alt.Tooltip("확인필요:Q", title="확인 필요")])
    layers = [bars, need]
    if len(d["markers"]):
        layers.append(alt.Chart(d["markers"]).mark_rule(color=MARK_COLOR, strokeDash=[4, 3], strokeWidth=1.5)
                      .encode(x="yearmonthdate(날짜):T", tooltip=["이름:N", "날짜:N"]))
        layers.append(alt.Chart(d["markers"]).mark_text(align="left", dx=4, baseline="top", dy=2, color=MARK_COLOR,
                                                        fontSize=11)
                      .encode(x="yearmonthdate(날짜):T", y=alt.value(0), text="이름:N"))
    return alt.layer(*layers).properties(height=height)


def recent_vs_usual(d, height=240):
    """채널별 평소 대비 최근 (30일 환산 건수)"""
    return alt.Chart(d["compare"]).mark_bar(cornerRadiusEnd=3).encode(
        y=alt.Y("채널:N", sort=CHANNEL_ORDER, title=None),
        yOffset=alt.YOffset("기간:N", sort=["평소", "최근"]),
        x=alt.X("30일환산:Q", title=None, axis=alt.Axis(gridOpacity=0.4)),
        color=alt.Color("기간:N", scale=alt.Scale(domain=["평소", "최근"], range=[BASE_GRAY, CHANNEL_COLORS[0]]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["채널:N", "기간:N", alt.Tooltip("30일환산:Q", format=".1f")]).properties(height=height)


def off_hours_share(d, height=240):
    """채널별 업무시간 외 비중 (%) — 단일 계열이라 범례 없이 값 라벨"""
    base = alt.Chart(d["offhours"]).encode(y=alt.Y("채널:N", sort=CHANNEL_ORDER, title=None))
    bars = base.mark_bar(cornerRadiusEnd=3, color=CHANNEL_COLORS[0]).encode(
        x=alt.X("비중(%):Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(gridOpacity=0.4)),
        tooltip=["채널:N", "전체:Q", "업무시간외:Q", "비중(%):Q"])
    labels = base.mark_text(align="left", dx=4, fontSize=11, color=MARK_COLOR).encode(
        x="비중(%):Q", text=alt.Text("비중(%):Q", format=".0f"))
    return (bars + labels).properties(height=height)


def daily_bytes(d, height=240):
    """일자별 전송 용량 (MB, 채널별 누적) — REQ-01 과 같은 기준(허용·삭제 제외·중복 1회)"""
    return alt.Chart(d["bytes"]).mark_bar(cornerRadiusTopLeft=1, cornerRadiusTopRight=1).encode(
        x=_date_x(), y=alt.Y("sum(MB):Q", title=None, axis=alt.Axis(gridOpacity=0.4)),
        color=_channel_color(), order=alt.Order("채널:N"),
        tooltip=[alt.Tooltip("날짜:N"), "채널:N", alt.Tooltip("sum(MB):Q", title="MB", format=",.1f")]
    ).properties(height=height)
