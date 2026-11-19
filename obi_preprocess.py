"""
OBI (Offboarding Inspector) 전처리 v2 - PoC용

역할: DDI 추출 엑셀(시트별 로그)을 읽어 '통합 타임라인'과 검증 결과를 만든다.
원칙: 계산·정리만 하고 판정(수상함 여부)은 하지 않는다. 판정은 LLM과 담당자가 한다.

사용법:
    python obi_preprocess.py 입력.xlsx 출력.xlsx --retire-date 2026-09-30 [--base-date 2026-09-02] [--account 계정]
"""
import argparse
import os
import re
from datetime import datetime, timedelta

import pandas as pd

import obi_criteria as crit

# ===========================================================================
# 설정값 (회사 기준에 맞게 조정)
# ===========================================================================
WORK_START_HOUR = 8          # 업무 시작 (08시)
WORK_END_HOUR = 19           # 업무 종료 (19시)
ANALYSIS_DAYS = 182          # 분석 기간: 기준일(로그 추출 요청일) 이전 약 6개월
RECENT_DAYS = 30             # 기준일 이전 '최근 기간' 일수 (나머지는 '평소 기간')
HOLIDAYS = {"2026-06-03", "2026-08-15", "2026-09-24", "2026-09-25", "2026-09-26"}
# 도메인·키워드 사전은 obi_criteria.py 한 곳에서 관리
INTERNAL_EMAIL_DOMAINS = tuple(crit.INTERNAL_DOMAINS)
PERSONAL_MAIL_DOMAINS = tuple(crit.PERSONAL_MAIL_DOMAINS)
APPROVED_SERVICES = tuple(crit.APPROVED_SERVICES)
NO_EVENT_MARKERS = {"이벤트 없음", "이벤트없음"}
USB_LINK_SECONDS = 120       # USB로그와 DLP USB Log를 같은 행위로 연결하는 시간 차
DLP_MERGE_SECONDS = 60       # DLP 규칙(RULE NAME)만 다른 같은 행위를 하나로 합치는 시간 차
# 인적 사항 자동 입력 (REQ-10). [결정 필요] 실제 DDI 추출본에서 부서가 있는 컬럼명 확인 전까지 후보만 둔다.
ACCOUNT_COLUMNS = ("Account", "ACCOUNT", "아이디")               # '홍길동(kildong.hong) 선임' 형태도 옴
NAME_COLUMNS = ("이름", "성명", "사용자명", "User Name", "USER NAME")
RANK_COLUMNS = ("직급", "직위", "Title", "TITLE")
DEPT_COLUMNS = ("부서", "부서명", "소속", "Department", "DEPARTMENT", "Dept")

NOISE_ATTACH = re.compile(r"^(header|textreadercomponent:\d+|image\d{3}\.(png|jpg|jpeg|gif))$", re.I)
FILE_TYPES = {
    "문서": ("doc", "docx", "hwp", "hwpx", "pdf", "txt", "ppt", "pptx", "msg", "eml"),
    "스프레드시트": ("xls", "xlsx", "xlsm", "csv"),
    "압축": ("zip", "7z", "rar", "egg", "alz", "tar", "gz"),
    "이미지": ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"),
    "설계·CAD": ("dwg", "dxf", "step", "stp", "igs", "iges", "catpart", "prt", "sldprt"),
    "소스코드": ("py", "java", "c", "cpp", "js", "ts", "sql", "cs"),
}


# ===========================================================================
# 공통 유틸
# ===========================================================================
def parse_time(v):
    """datetime, '2026. 7. 30. 오전 2:34:09', 'Begin: 2026-07-29 10:21:50' 등을 처리"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NaT
    if isinstance(v, datetime):
        return pd.Timestamp(v.replace(tzinfo=None))   # 엑셀 연동 시 붙는 시간대 정보 제거
    s = re.sub(r"^(Begin|End Time|End)\s*:\s*", "", str(v).strip())
    m = re.match(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(오전|오후)\s*(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        y, mo, d, ampm, h, mi, se = m.groups()
        h = int(h) % 12 + (12 if ampm == "오후" else 0)
        return pd.Timestamp(int(y), int(mo), int(d), h, int(mi), int(se))
    return pd.to_datetime(s, errors="coerce")


def is_off_hours(ts):
    if pd.isna(ts):
        return False
    if ts.weekday() >= 5 or ts.strftime("%Y-%m-%d") in HOLIDAYS:
        return True
    return not (WORK_START_HOUR <= ts.hour < WORK_END_HOUR)


def domain_of(text):
    if not isinstance(text, str):
        return ""
    if "@" in text:
        return text.split("@")[-1].strip().lower()
    m = re.match(r"^(?:https?://)?([^/:\s]+)", text.strip())
    return m.group(1).lower() if m else ""


def matches_domain(dom, domains):
    return any(dom == d or dom.endswith("." + d) for d in domains)


def norm_account(v):
    """계정 대소문자·공백 차이 제거. '홍길동(kildong.hong) 선임' 형태도 처리"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    m = re.search(r"\(([A-Za-z0-9._-]+)\)", s)
    return (m.group(1) if m else s).lower()


def file_basename(v):
    s = str(v or "").strip()
    return re.split(r"[\\/]", s)[-1] if s else ""


def file_ext(name):
    m = re.search(r"\.([A-Za-z][A-Za-z0-9]{0,7})$", str(name).strip())
    return m.group(1).lower() if m else ""


def file_type(name):
    ext = file_ext(name)
    for t, exts in FILE_TYPES.items():
        if ext in exts:
            return t
    return "기타" if ext else ""


FILE_SEP = "\n"   # 파일명에 쉼표가 들어갈 수 있어 줄바꿈으로 구분해 저장


def join_files(files):
    """파일명 목록 → 저장용 문자열 (빈 값·NaN 제외, 순서 유지, 중복 제거)"""
    out = []
    for f in files or []:
        if f is None or (isinstance(f, float) and pd.isna(f)):
            continue
        s = str(f).strip()
        if s and s not in out:
            out.append(s)
    return FILE_SEP.join(out)


def split_files(value):
    """저장된 파일목록 문자열(또는 이미 list) → 파일명 list"""
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [x.strip() for x in str(value).split(FILE_SEP) if x.strip()]


def file_key(name):
    """교차 채널 비교용 파일 이름 정규화: 접두어·복사본 표시·확장자·기호 제거"""
    s = file_basename(name).lower()
    s = re.sub(r"^microsoft (word|excel|powerpoint) - ", "", s)
    s = re.sub(r"\.[a-z0-9]{1,8}$", "", s)
    s = re.sub(r"(의 복사본|복사본|- copy|\(\d+\)|_v?\d+(\.\d+)*$)", "", s)
    s = re.sub(r"[\s_\-.()\[\]]+", "", s)
    return s if len(s) >= 4 else ""   # 너무 짧은 이름은 오탐 연결 방지


class LogWorkbook:
    """로그 엑셀 읽기. 일반 파일은 pandas로, 암호화(DRM·민감도 레이블) 파일은 PC의 엑셀을 통해 읽는다."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.sheets = {}
        with open(self.path, "rb") as f:
            head = f.read(8)
        if head.startswith(b"PK"):
            self.mode = "일반 xlsx (pandas)"
            self.sheets = self._read_pandas("openpyxl")
        else:
            try:  # 옛 xls 형식이면 xlrd로 읽힘
                self.sheets = self._read_pandas("xlrd")
                self.mode = "옛 xls (pandas)"
            except Exception:
                self.mode = "암호화 파일 (엑셀 연동)"
                self._read_via_excel()
        self.sheet_names = list(self.sheets)

    def _read_pandas(self, engine):
        # with 로 닫아야 Windows 에서 업로드 임시 파일이 잠기지 않는다
        with pd.ExcelFile(self.path, engine=engine) as xl:
            return {n: pd.read_excel(xl, sheet_name=n, header=None) for n in xl.sheet_names}

    def _read_via_excel(self):
        try:
            import pythoncom
            import win32com.client as win32
        except ImportError:
            raise SystemExit("암호화된 엑셀 파일입니다. 터미널에서 'pip install pywin32' 설치 후 다시 실행하세요.")
        pythoncom.CoInitialize()
        try:
            excel = win32.DispatchEx("Excel.Application")   # 사용 중인 엑셀과 분리된 별도 창
        except Exception as e:
            pythoncom.CoUninitialize()
            raise SystemExit(
                "업로드한 파일이 암호화(DRM·민감도 레이블)된 엑셀입니다.\n"
                "이 파일을 열려면 이 PC에 Microsoft Excel이 설치되어 있어야 합니다.\n\n"
                f"COM 오류: {e}\n\n"
                "해결 방법:\n"
                "① Excel이 설치된 PC에서 앱을 실행하거나\n"
                "② 파일을 Excel에서 직접 열어 '다른 이름으로 저장 → xlsx'로 저장한 뒤 다시 업로드하세요."
            )
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = None
        try:
            wb = excel.Workbooks.Open(self.path, UpdateLinks=0, ReadOnly=True)
            for ws in wb.Worksheets:
                used = ws.UsedRange
                val = used.Value
                if val is None:
                    rows = [[None]]
                elif not isinstance(val, tuple):
                    rows = [[val]]
                else:
                    rows = [list(r) for r in val]
                # UsedRange가 A1에서 시작하지 않으면 앞을 빈칸으로 채워 원본 행번호를 유지
                r0, c0 = used.Row - 1, used.Column - 1
                width = max(len(r) for r in rows) + c0
                rows = [[None] * width for _ in range(r0)] + [[None] * c0 + r for r in rows]
                self.sheets[ws.Name] = pd.DataFrame(rows)
        finally:
            if wb is not None:
                wb.Close(SaveChanges=False)
            excel.Quit()
            pythoncom.CoUninitialize()


def find_sheet(xls, prefix):
    key = prefix.replace(" ", "").lower()
    for name in xls.sheet_names:
        if name.replace(" ", "").lower().startswith(key):
            return name
    return None


def read_sheet(xls, name, header_key):
    raw = xls.sheets[name]
    first = str(raw.iat[0, 0]).strip() if raw.size else ""
    if first in NO_EVENT_MARKERS or raw.dropna(how="all").empty:
        return "이벤트 없음", None
    hdr = None
    for i in range(min(10, len(raw))):
        if header_key in [str(x).strip() for x in raw.iloc[i].tolist()]:
            hdr = i
            break
    if hdr is None:
        return "형식 확인 필요 (헤더를 찾지 못함)", None
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df.loc[:, [c for c in df.columns if c and c != "nan"]]
    df["_원본행"] = df.index + 1
    df = df.dropna(how="all", subset=[c for c in df.columns if c != "_원본행"])
    return "로그 있음", df


def rows_label(rows):
    """[3,4,5,9,10] → '3-5, 9-10 (5행)' : 떨어진 행도 정확히 표시"""
    rows = sorted({int(r) for r in rows})
    if len(rows) == 1:
        return str(rows[0])
    parts, start, prev = [], rows[0], rows[0]
    for r in rows[1:] + [None]:
        if r is not None and r == prev + 1:
            prev = r
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if r is not None:
            start = prev = r
    return f"{', '.join(parts)} ({len(rows)}행)"


def ev(**kw):
    base = dict(시간=pd.NaT, 채널="", 행위="", 대상="", 상세="", 계정="", 파일목록=[],
                용량_bytes=None, 페이지수=None, 첨부수=None, 차단여부="허용", 개인메일="",
                수신자="", 탐지규칙="", 원본시트="", 원본행="", _행수=1)
    base.update(kw)
    return base


# ===========================================================================
# 채널별 변환
# ===========================================================================
def conv_web(df, sheet):
    out = []
    for _, r in df.iterrows():
        dom = domain_of(r.get("URL"))
        out.append(ev(시간=parse_time(r.get("시간")), 채널="웹", 행위="웹 접속", 대상=r.get("URL"),
                      상세=f"분류: {r.get('Sub Category')}", 계정=norm_account(r.get("Account")),
                      용량_bytes=r.get("SentBytes"),
                      차단여부="차단" if "거부" in str(r.get("Block/Allow")) else "허용",
                      개인메일="Y" if matches_domain(dom, PERSONAL_MAIL_DOMAINS) and
                      ("mail" in dom or "email" in str(r.get("Sub Category")).lower()) else "",
                      원본시트=sheet, 원본행=str(r["_원본행"])))
    return out


def conv_print(df, sheet):
    out = []
    for _, r in df.iterrows():
        printer = str(r.get("프린터명") or "")
        pages = pd.to_numeric(r.get("출력페이지"), errors="coerce")
        copies = pd.to_numeric(r.get("출력매수"), errors="coerce")
        fname = r.get("출력파일")
        t = parse_time(r.get("출력시간(PC)"))
        if pd.isna(t):   # 빈 칸은 NaN(참)이라 `or` 로는 서버 시간으로 넘어가지 않는다
            t = parse_time(r.get("출력시간(서버)"))
        out.append(ev(시간=t, 채널="출력",
                      행위="PDF 변환(가상 프린터)" if "pdf" in printer.lower() else "종이 출력",
                      대상=fname, 파일목록=[fname], 계정=norm_account(r.get("아이디")),
                      상세=f"프로그램: {r.get('응용프로그램')} | 문서등급: {r.get('출력형식')} | 프린터: {printer}",
                      페이지수=(pages * copies) if pd.notna(pages) and pd.notna(copies) else None,
                      원본시트=sheet, 원본행=str(r["_원본행"])))
    return out


def conv_usb(df, sheet):
    out = []
    for _, r in df.iterrows():
        t = parse_time(r.get("Begin Time"))
        if pd.isna(t):
            t = parse_time(r.get("Time"))
        dev = re.sub(r"^Device ID:\s*", "", str(r.get("Device ID") or ""))
        fname = r.get("File Name")
        out.append(ev(시간=t, 채널="USB", 행위=f"USB {r.get('Action')}", 대상=fname,
                      파일목록=[fname], 계정=norm_account(r.get("Account")),
                      상세=f"프로세스: {r.get('Process Name')} | 장치: {dev} | 기록시각: {r.get('Time')}",
                      용량_bytes=r.get("File Size"),
                      차단여부="차단" if str(r.get("Block/Continue")).lower() == "block" else "허용",
                      원본시트=sheet, 원본행=str(r["_원본행"])))
    return out


def conv_dlp(df, sheet, channel):
    """DLP 로그는 '수신자 × 첨부' 단위로 행이 늘어나므로 INCIDENT ID로 묶어 1건으로 만든다."""
    out = []
    df = df.copy()
    df["_noise"] = df["ATTACHED FILE"].astype(str).str.strip().str.match(NOISE_ATTACH)
    df["_t"] = df["TIME"].apply(parse_time)

    # 같은 메일·업로드·반출이 DLP 규칙마다 다른 INCIDENT ID로 기록되는 경우를 하나로 합친다.
    # 기준: 계정 + (메일: 제목·발신자 / 웹: URL / USB: 대상 경로)이 같고 시간 차가 60초 이내
    def target_sig(g):
        acc = norm_account(g["ACCOUNT"].iloc[0])
        if channel == "이메일":
            # SA팀 기준: TIME + SUBJECT + SENDER 로 같은 메일 판단
            return (acc, str(g["EMAIL SUBJECT"].iloc[0]).strip(), str(g["SENDER"].iloc[0]).strip().lower())
        if channel == "웹 업로드":
            return (acc, str(g["RECIPIENTS or URL"].iloc[0]).strip().lower())
        return (acc, str(g["TARGET PATH"].dropna().astype(str).iloc[0] if g["TARGET PATH"].notna().any() else ""))

    incidents = [(inc, g["_t"].min(), target_sig(g)) for inc, g in df.groupby("INCIDENT ID", sort=False)]
    incidents.sort(key=lambda x: (str(x[2]), x[1]))
    cluster_of, last = {}, {}
    for inc, t, sig in incidents:
        prev = last.get(sig)
        if prev and pd.notna(t) and pd.notna(prev[1]) and (t - prev[1]).total_seconds() <= DLP_MERGE_SECONDS:
            cluster_of[inc] = prev[0]
        else:
            cluster_of[inc] = inc
        last[sig] = (cluster_of[inc], t)
    df["_묶음"] = df["INCIDENT ID"].map(cluster_of)

    for _, g in df.groupby("_묶음", sort=False):
        real = g[~g["_noise"]]
        files = list(dict.fromkeys(real["ATTACHED FILE"].dropna().astype(str)))
        size = pd.to_numeric(real.drop_duplicates("ATTACHED FILE")["FILE SIZE"], errors="coerce").sum()
        inline = g["ATTACHED FILE"].astype(str).str.match(r"^image\d{3}\.", case=False).any()
        rules = list(dict.fromkeys(g["RULE NAME"].dropna().astype(str).str.strip())) if "RULE NAME" in g else []
        n_inc = g["INCIDENT ID"].nunique()
        base = dict(시간=g["_t"].min(), 채널=channel, 탐지규칙=" / ".join(rules)
                    + (f" (인시던트 {n_inc}건 병합)" if n_inc > 1 else ""), 파일목록=files, 첨부수=len(files),
                    계정=norm_account(g["ACCOUNT"].iloc[0]), 용량_bytes=size,
                    차단여부="차단" if (g["BLOCKEDSTATUS"].astype(str).str.lower() != "allowed").any() else "허용",
                    원본시트=sheet, 원본행=rows_label(g["_원본행"]), _행수=len(g))
        flist = ", ".join(files) if files else "없음"
        if channel == "이메일":
            rcpt = list(dict.fromkeys(g["RECIPIENTS or URL"].dropna().astype(str).str.strip().str.lower()))
            doms = sorted({domain_of(x) for x in rcpt})
            external = [d for d in doms if not matches_domain(d, INTERNAL_EMAIL_DOMAINS)]
            out.append(ev(**base, 행위="외부 메일 발송" if external else "사내 메일 발송", 수신자=", ".join(rcpt),
                          대상=f"제목: {g['EMAIL SUBJECT'].iloc[0]}",
                          개인메일="Y" if any(matches_domain(d, PERSONAL_MAIL_DOMAINS) for d in doms) else "",
                          상세=f"수신 {len(rcpt)}명: {', '.join(rcpt)} | 첨부 {len(files)}개: {flist}"
                               + (" | 서명 이미지 제외" if inline else "")))
        elif channel == "웹 업로드":
            url = g["RECIPIENTS or URL"].iloc[0]
            dom = domain_of(url)
            out.append(ev(**base, 행위="웹 파일 업로드", 대상=url, 상세=f"파일: {flist}",
                          개인메일="Y" if matches_domain(dom, PERSONAL_MAIL_DOMAINS) else ""))
        else:
            path = g["TARGET PATH"].dropna().astype(str)
            out.append(ev(**base, 행위="USB 파일 반출(DLP)", 대상=path.iloc[0] if len(path) else "",
                          상세=f"파일: {flist} | 프로그램: {g['APPLICATION'].iloc[0]}"))
    return out


SHEETS = [  # (시트명 앞부분, 헤더 식별 컬럼, 변환 함수, 필수 여부)
    ("Web Log", "URL", conv_web, True),
    ("USB로그", "Action", conv_usb, True),
    ("출력물로그", "출력파일", conv_print, True),
    ("DLP Email Log", "INCIDENT ID", lambda d, s: conv_dlp(d, s, "이메일"), True),
    ("DLP Web Log", "INCIDENT ID", lambda d, s: conv_dlp(d, s, "웹 업로드"), True),
    ("DLP USB Log", "INCIDENT ID", lambda d, s: conv_dlp(d, s, "USB(DLP)"), False),
]


# ===========================================================================
# 인적 사항 추출 (REQ-10)
# ===========================================================================
_PERSON_RE = re.compile(r"^\s*([^\s()]+)\s*\(\s*([A-Za-z0-9._-]+)\s*\)\s*(\S.*)?$")


def _pick(counter):
    """가장 많이 나온 값 (동률이면 먼저 나온 값). 값이 여러 개면 갈림=True"""
    if not counter:
        return "", False
    best = max(counter.items(), key=lambda kv: kv[1])[0]   # dict 는 삽입 순서 → 동률 시 먼저 나온 값
    return best, len(counter) > 1


def extract_person(xls):
    """로그에서 이름·직급·부서·계정을 뽑는다.

    돌려주는 값: {"이름", "직급", "부서", "계정": str, "갈림": [항목명...], "후보": {항목: {값: 건수}}}
    로그에 없는 항목은 빈 문자열. 값이 여러 개면 가장 많이 나온 값 + '갈림'에 항목명.
    """
    seen = {k: {} for k in ("이름", "직급", "부서", "계정")}

    def add(key, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return
        s = str(v).strip()
        if s and s.lower() != "nan":
            seen[key][s] = seen[key].get(s, 0) + 1

    for prefix, header_key, _fn, _req in SHEETS:
        name = find_sheet(xls, prefix)
        if name is None:
            continue
        _st, df = read_sheet(xls, name, header_key)
        if df is None:
            continue
        for col in df.columns:
            if col in ACCOUNT_COLUMNS:
                for v in df[col].dropna():
                    m = _PERSON_RE.match(str(v))
                    if m:
                        add("이름", m.group(1))
                        add("직급", m.group(3))
                    add("계정", norm_account(v))
            elif col in NAME_COLUMNS:
                df[col].dropna().map(lambda v: add("이름", v))
            elif col in RANK_COLUMNS:
                df[col].dropna().map(lambda v: add("직급", v))
            elif col in DEPT_COLUMNS:
                df[col].dropna().map(lambda v: add("부서", v))

    out, split = {}, []
    for k, counter in seen.items():
        out[k], is_split = _pick(counter)
        if is_split:
            split.append(k)
    out["갈림"] = split
    out["후보"] = seen
    return out


def person_check(person):
    """검증결과에 넣을 (항목, 결과, 내용)"""
    found = [f"{k} {person[k]}" for k in ("이름", "직급", "부서") if person.get(k)]
    if person.get("갈림"):
        detail = " / ".join(f"{k}: " + ", ".join(f"{v}({n})" for v, n in person["후보"][k].items())
                            for k in person["갈림"])
        return ("인적 사항", f"로그 값이 여러 개 ({', '.join(person['갈림'])}) - 확인 필요", detail)
    return ("인적 사항", "로그에서 확인" if found else "로그에 없음 (직접 입력)", ", ".join(found))


# ===========================================================================
# 메인
# ===========================================================================
def run(path_in, path_out, retire_date, base_date=None, person_account=None):
    """path_in 은 파일 경로 또는 이미 읽은 LogWorkbook (업로드 파일을 한 번만 읽기 위함)"""
    retire = pd.Timestamp(retire_date)
    xls = path_in if isinstance(path_in, LogWorkbook) else LogWorkbook(path_in)
    print(f"[파일 읽기] {xls.mode}")
    events, status, checks = [], [], []

    # --- 시트 읽기 + 행 수 대사 ---
    for prefix, key, fn, required in SHEETS:
        name = find_sheet(xls, prefix)
        if name is None:
            st = "시트 없음 - 확인 필요" if required else "이벤트 없음 (시트 없음)"
            status.append(dict(시트=prefix, 상태=st, 원본행수=0, 이벤트수=0, 반영행수=0, 대사결과="-"))
            continue
        st, df = read_sheet(xls, name, key)
        e = fn(df, name) if df is not None else []
        n_raw = 0 if df is None else len(df)
        n_used = sum(x["_행수"] for x in e)
        status.append(dict(시트=name, 상태=st, 원본행수=n_raw, 이벤트수=len(e), 반영행수=n_used,
                           대사결과="일치" if n_raw == n_used else "불일치 - 확인 필요"))
        events += e

    if not events:
        tl = pd.DataFrame(columns=["이벤트ID"])
    else:
        tl = pd.DataFrame(events).sort_values("시간", kind="stable").reset_index(drop=True)
        tl.insert(0, "이벤트ID", [f"E{i + 1:04d}" for i in range(len(tl))])
    # 기준일: 로그 추출 요청일. 입력이 없으면 로그의 마지막 날짜를 사용
    if base_date:
        base = pd.Timestamp(base_date)
    elif len(tl) and tl["시간"].notna().any():
        base = tl["시간"].max().normalize()
    else:
        base = retire
    period_start = base - timedelta(days=ANALYSIS_DAYS)
    recent_start = base - timedelta(days=RECENT_DAYS)
    base_end = base + timedelta(days=1)
    if len(tl):
        # --- 사실 표시 ---
        tl["기간"] = tl["시간"].apply(lambda t: "최근" if pd.notna(t) and t >= recent_start else "평소")
        tl["업무시간외"] = tl["시간"].apply(lambda t: "Y" if is_off_hours(t) else "")
        tl["승인서비스"] = tl["대상"].astype(str).str.lower().apply(
            lambda x: "Y" if any(a in x for a in APPROVED_SERVICES) else "")
        tl["파일유형"] = tl["파일목록"].apply(
            lambda fs: ", ".join(dict.fromkeys(t for t in (file_type(f) for f in fs) if t)))

        # 최초 등장: 채널별 비교 대상(도메인/수신 도메인/장치/프로그램)이 최근 기간에 처음 나타남
        def cmp_key(r):
            if r["채널"] in ("웹", "웹 업로드"):
                return domain_of(str(r["대상"]))
            if r["채널"] == "이메일":
                return ",".join(sorted({domain_of(x) for x in re.findall(r"[\w.+-]+@[\w.-]+", r["상세"])}))
            if r["채널"] == "USB":
                return re.search(r"장치: ([^|]*)", r["상세"]).group(1).strip()
            if r["채널"] == "출력":
                return re.search(r"프린터: (.*)$", r["상세"]).group(1).strip().lower()
            return ""
        seen, first = set(), []
        for _, r in tl.iterrows():
            k = (r["채널"], cmp_key(r))
            first.append("Y" if k[1] and k not in seen and r["기간"] == "최근" else "")
            seen.add(k)
        tl["최초등장"] = first

        # 교차 채널 연결: 같은 파일(정규화 이름)이 다른 채널에 등장 / USB-DLP USB 동일 행위
        links = {eid: set() for eid in tl["이벤트ID"]}
        dups = {eid: set() for eid in tl["이벤트ID"]}
        by_key = {}
        for _, r in tl.iterrows():
            for f in r["파일목록"]:
                k = file_key(f)
                if k:
                    by_key.setdefault(k, []).append((r["이벤트ID"], r["채널"]))
        for k, items in by_key.items():
            if len({c for _, c in items}) >= 2:
                ids = {i for i, _ in items}
                for i in ids:
                    links[i] |= ids - {i}
        usb = tl[tl["채널"] == "USB"]
        dlp_usb = tl[tl["채널"] == "USB(DLP)"]
        for _, a in usb.iterrows():
            for _, b in dlp_usb.iterrows():
                if pd.notna(a["시간"]) and pd.notna(b["시간"]) and \
                        abs((a["시간"] - b["시간"]).total_seconds()) <= USB_LINK_SECONDS:
                    dups[a["이벤트ID"]].add(b["이벤트ID"])
                    dups[b["이벤트ID"]].add(a["이벤트ID"])
        # 동일파일: 같은 파일이 다른 채널(출력·메일·USB·웹 업로드)에서도 등장
        # 중복기록: USB로그와 DLP USB Log에 같은 행위가 각각 기록된 경우
        tl["동일파일"] = tl["이벤트ID"].apply(lambda i: ", ".join(sorted(links[i])))
        tl["중복기록"] = tl["이벤트ID"].apply(lambda i: ", ".join(sorted(dups[i])))

        # 일별 집중도
        tl["날짜"] = tl["시간"].dt.strftime("%Y-%m-%d")
        tl["당일건수"] = tl.groupby("날짜")["이벤트ID"].transform("count")

    # --- SA팀 점검 기준 적용 ---
    crit_table = pd.DataFrame()
    if len(tl):
        tl, crit_table = crit.apply_criteria(tl)

    # --- 검증 ---
    accounts = sorted({a for a in tl.get("계정", pd.Series(dtype=str)) if a})
    if person_account:
        others = [a for a in accounts if a != person_account.lower()]
        checks.append(("계정", "일치" if not others else "다른 계정 포함 - 확인 필요",
                       f"입력 계정: {person_account.lower()} / 로그 계정: {', '.join(accounts)}"))
    else:
        checks.append(("계정", "단일 계정" if len(accounts) <= 1 else "복수 계정 - 확인 필요",
                       f"로그에서 확인된 계정(대소문자 통일): {', '.join(accounts) or '없음'}"))
    if len(tl) and "시간" in tl:
        out_range = tl[(tl["시간"] < period_start) | (tl["시간"] > base_end)]
        checks.append(("분석 기간", "기간 내" if out_range.empty else f"기간 밖 {len(out_range)}건 - 확인 필요",
                       f"기준 {period_start.date()} ~ {base.date()} / 로그 {tl['시간'].min()} ~ {tl['시간'].max()}"))
        no_time = int(tl["시간"].isna().sum())
        checks.append(("시간 변환", "정상" if no_time == 0 else f"시간 해석 실패 {no_time}건 - 확인 필요", ""))
    checks.append(person_check(extract_person(xls)))
    bad = [s for s in status if s["대사결과"].startswith("불일치") or "확인 필요" in s["상태"]]
    checks.append(("시트·행 수 대사", "정상" if not bad else "확인 필요", ", ".join(s["시트"] for s in bad)))

    # --- 요약 ---
    summary = []
    if len(tl):
        base_days = max((recent_start - period_start).days, 1)
        recent_days = max((base_end - recent_start).days, 1)
        for ch, g in tl.groupby("채널"):
            b, r = g[g["기간"] == "평소"], g[g["기간"] == "최근"]
            summary.append(dict(
                채널=ch, 평소_건수=len(b), 최근_건수=len(r),
                평소_30일환산=round(len(b) / base_days * 30, 1), 최근_30일환산=round(len(r) / recent_days * 30, 1),
                업무시간외=int((g["업무시간외"] == "Y").sum()), 최초등장=int((g["최초등장"] == "Y").sum()),
                개인메일=int((g["개인메일"] == "Y").sum()), 차단=int((g["차단여부"] == "차단").sum()),
                동일파일=int((g["동일파일"] != "").sum())))
        daily = (tl.pivot_table(index="날짜", columns="채널", values="이벤트ID", aggfunc="count", fill_value=0)
                 .assign(합계=lambda d: d.sum(axis=1)).sort_values("합계", ascending=False).reset_index())
    else:
        daily = pd.DataFrame()

    if len(tl):
        tl["파일목록"] = tl["파일목록"].apply(join_files)
    cols = ["이벤트ID", "시간", "점검결과", "점검사유", "기간", "채널", "행위", "대상", "상세", "파일목록", "파일유형", "용량_bytes",
            "페이지수", "첨부수", "차단여부", "업무시간외", "최초등장", "개인메일", "승인서비스",
            "동일파일", "중복기록", "당일건수", "수신자", "탐지규칙", "계정", "원본시트", "원본행"]
    tl_out = tl.reindex(columns=cols)
    cond = pd.DataFrame([
        ("퇴직(예정)일", str(retire.date())),
        ("기준일", f"{base.date()} ({'입력값' if base_date else '로그 마지막 날짜'})"),
        ("분석 기간", f"{period_start.date()} ~ {base.date()}"),
        ("최근 기간", f"{recent_start.date()} ~ {base.date()} (기준일 이전 {RECENT_DAYS}일)"),
        ("업무시간", f"평일 {WORK_START_HOUR:02d}~{WORK_END_HOUR:02d}시, 주말·공휴일은 업무시간외"),
        ("원칙", "이 파일은 사실 정리 결과이며 이상징후 판정을 포함하지 않음"),
    ], columns=["항목", "값"])

    with pd.ExcelWriter(path_out, engine="openpyxl") as w:
        pd.DataFrame(checks, columns=["확인 항목", "결과", "내용"]).to_excel(w, sheet_name="검증결과", index=False)
        crit_table.to_excel(w, sheet_name="기준점검", index=False)
        tl_out.to_excel(w, sheet_name="통합타임라인", index=False)
        pd.DataFrame(summary).to_excel(w, sheet_name="채널요약", index=False)
        daily.to_excel(w, sheet_name="일별건수", index=False)
        pd.DataFrame(status).to_excel(w, sheet_name="시트상태", index=False)
        cond.to_excel(w, sheet_name="분석조건", index=False)
    style_workbook(path_out)
    return checks, status, tl_out


def style_workbook(path):
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = load_workbook(path)
    widths = {"통합타임라인": {"B": 19, "G": 45, "H": 60, "R": 14}, "검증결과": {"A": 16, "B": 26, "C": 80},
              "분석조건": {"A": 14, "B": 60}, "시트상태": {"A": 18, "B": 26, "F": 18}}
    for ws in wb:
        for col in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(col)].width = 11
        for c, w in widths.get(ws.title, {}).items():
            ws.column_dimensions[c].width = w
        for row in ws.iter_rows():
            for cell in row:
                cell.font = Font(name="Arial", size=10, bold=cell.row == 1)
                cell.alignment = Alignment(vertical="top", wrap_text=ws.title == "통합타임라인" and cell.column in (7, 8))
                if cell.row == 1:
                    cell.fill = PatternFill("solid", fgColor="E3E8EF")
                elif isinstance(cell.value, str) and "확인 필요" in cell.value:
                    cell.fill = PatternFill("solid", fgColor="FCE4E4")
        if ws.title == "통합타임라인":
            for r in range(2, ws.max_row + 1):
                ws.cell(r, 2).number_format = "yyyy-mm-dd hh:mm:ss"
        ws.freeze_panes = "A2"
    wb.save(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OBI 전처리")
    ap.add_argument("input", help="DDI 추출 로그 엑셀")
    ap.add_argument("output", help="결과 엑셀 경로")
    ap.add_argument("--retire-date", required=True, help="퇴직(예정)일 YYYY-MM-DD")
    ap.add_argument("--base-date", help="기준일=로그 추출 요청일 YYYY-MM-DD (선택, 없으면 로그 마지막 날짜)")
    ap.add_argument("--account", help="퇴직자 계정 (선택, 입력 시 로그 계정과 대조)")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    checks, status, tl = run(a.input, a.output, a.retire_date, a.base_date, a.account)
    print("[검증결과]")
    for item, result, detail in checks:
        print(f"  - {item}: {result}  {detail}")
    print(f"\n[시트] " + " / ".join(f"{s['시트']} {s['원본행수']}행→{s['이벤트수']}건" for s in status))
    print(f"\n완료: {a.output} (이벤트 {len(tl)}건)")
