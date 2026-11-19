"""
하네스용 합성 DDI 로그 생성기 (실제 개인정보 없음)

- 실제 DDI 추출 엑셀과 같은 시트명·헤더 구조를 흉내 낸다.
- B1~B9 / R1~R6 점검 코드와 교차 채널 연결·DLP 병합·시간 형식 변형 등
  전처리의 주요 분기를 한 파일에서 모두 지나가도록 구성했다.
- 엑셀 파일은 커밋하지 않고(.gitignore: *.xlsx) 테스트 실행 시마다 이 코드로 다시 만든다.
  → 같은 코드 = 같은 입력 이 보장된다.

시나리오를 추가할 때는 SCENARIO_NOTES 에 의도를 한 줄 적고 골든을 갱신한다
(python tests/harness.py update).
"""
from datetime import datetime

from openpyxl import Workbook

ACCOUNT = "kildong.hong"
RETIRE_DATE = "2026-09-30"
BASE_DATE = "2026-09-02"

SCENARIO_NOTES = {
    "web": "사내(R6)·개인 클라우드(B4)·개인메일 사이트(B4)·채용(R5)·차단(R1)·공공(R3, 공휴일)·한국어 시간 문자열",
    "usb": "USB 쓰기/삭제(B5)·자료 키워드(B3 도면)·차단(R1)·DLP USB 와 중복기록",
    "print": "보안 키워드+50매 이상(B1·B6)·반복 출력(B7)·개인 문서(R4)·모호한 파일명(B9)·PC 시간 누락→서버 시간",
    "dlp_email": "개인메일 첨부(B2·B3)·같은 메일 규칙별 인시던트 병합·사내 동반(R2)·공공기관(R3)·차단(R1)"
                 "·첨부 없는 개인메일(REQ-05)·타사+사내 동반(R2)·개인메일+본인 주소만(B2)·보안 키워드+사내 동반"
                 "(B1 유지·행위 맥락 낮음, 하위 도메인 hq.doosan.com)(REQ-11)",
    "dlp_web": "개인 클라우드 업로드(B4·B3)·승인 서비스(R6)·개인메일 사이트 업로드(REQ-05)",
    "dlp_usb": "USB 반출(B5·B3) — USB로그와 120초 이내 → 중복기록",
    "size": "REQ-01 B10: 웹 접속 120MB(단일), 업로드 정확히 100MB(경계 포함), USB 99.9MB(경계 미만), "
            "하루 6×90MB=540MB(하루 합계), 차단된 200MB(R1만)",
}

MB = 1024 * 1024

DLP_HEADER = ["INCIDENT ID", "TIME", "ACCOUNT", "EMAIL SUBJECT", "SENDER", "RECIPIENTS or URL",
              "ATTACHED FILE", "FILE SIZE", "RULE NAME", "BLOCKEDSTATUS", "TARGET PATH", "APPLICATION"]


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _sheet(wb, name, header, rows, title=None):
    ws = wb.create_sheet(name)
    if title:                       # 헤더 위 제목 행 → read_sheet 의 헤더 탐색 경로 검증
        ws.append([title])
    ws.append(header)
    for r in rows:
        ws.append(r)


def build(path):
    wb = Workbook()
    wb.remove(wb.active)
    me = "홍길동(Kildong.Hong) 선임"

    _sheet(wb, "Web Log", ["시간", "URL", "Sub Category", "Account", "SentBytes", "Block/Allow"], [
        [_dt("2026-05-12 10:00:00"), "https://www.doosan.com/main", "Business", me, 1200, "허용"],
        [_dt("2026-08-20 22:30:00"), "https://mybox.naver.com/upload", "Personal Storage", ACCOUNT, 5_000_000, "허용"],
        [_dt("2026-08-21 11:00:00"), "https://mail.naver.com/write", "Web-based Email", ACCOUNT, 300_000, "허용"],
        [_dt("2026-08-22 14:00:00"), "https://www.saramin.co.kr/zf_user", "Job Search", ACCOUNT, 800, "허용"],
        [_dt("2026-07-01 09:30:00"), "https://drive.google.com/drive", "Personal Storage", ACCOUNT, 0, "거부"],
        [_dt("2026-06-03 10:00:00"), "https://www.law.go.kr/main", "Government", ACCOUNT, 500, "허용"],
        ["2026. 8. 23. 오후 3:10:00", "https://news.example.com/a", "News", ACCOUNT, 100, "허용"],
        [_dt("2026-08-31 23:10:00"), "https://www.youtube.com/upload", "Streaming Media", ACCOUNT, 120 * MB, "허용"],
    ], title="Web Log 추출 결과")

    _sheet(wb, "USB로그", ["Begin Time", "Time", "Device ID", "File Name", "Action", "Account",
                          "Process Name", "File Size", "Block/Continue"], [
        ["Begin: 2026-08-25 19:40:00", "2026-08-25 19:40:05", "Device ID: USB\\VID_0781&PID_5581",
         "E:\\설계도면_v2.dwg", "Write", ACCOUNT, "explorer.exe", 2_048_000, "Continue"],
        ["Begin: 2026-08-25 19:41:00", "2026-08-25 19:41:02", "Device ID: USB\\VID_0781&PID_5581",
         "E:\\설계도면_v2.dwg", "Delete", ACCOUNT, "explorer.exe", 2_048_000, "Continue"],
        ["Begin: 2026-04-10 13:00:00", "2026-04-10 13:00:01", "Device ID: USB\\VID_1111&PID_2222",
         "E:\\report.xlsx", "Write", ACCOUNT, "excel.exe", 10_000, "Block"],
        ["Begin: 2026-08-14 10:00:00", "2026-08-14 10:00:01", "Device ID: USB\\VID_0781&PID_5581",
         "E:\\backup_99.zip", "Write", ACCOUNT, "explorer.exe", int(99.9 * MB), "Continue"],
        ["Begin: 2026-08-18 09:00:00", "2026-08-18 09:00:01", "Device ID: USB\\VID_0781&PID_5581",
         "E:\\big.iso", "Write", ACCOUNT, "explorer.exe", 200 * MB, "Block"],
    ] + [
        [f"Begin: 2026-08-19 14:0{k}:00", f"2026-08-19 14:0{k}:01", "Device ID: USB\\VID_0781&PID_5581",
         f"E:\\현장영상_0{k}.mp4", "Write", ACCOUNT, "explorer.exe", 90 * MB, "Continue"] for k in range(1, 7)
    ])

    _sheet(wb, "출력물로그", ["출력시간(PC)", "출력시간(서버)", "프린터명", "출력페이지", "출력매수", "출력파일",
                           "아이디", "응용프로그램", "출력형식"], [
        [_dt("2026-08-26 10:00:00"), _dt("2026-08-26 10:00:02"), "Microsoft Print to PDF", 30, 2,
         "대외비_사업계획.pdf", ACCOUNT, "Acrobat", "대외비"],
        [_dt("2026-08-26 10:05:00"), _dt("2026-08-26 10:05:01"), "Microsoft Print to PDF", 1, 1,
         "대외비_사업계획.pdf", ACCOUNT, "Acrobat", "대외비"],
        [_dt("2026-05-20 09:10:00"), _dt("2026-05-20 09:10:01"), "3F-Color-01", 1, 1,
         "재직증명서.pdf", ACCOUNT, "Acrobat", "일반"],
        [None, _dt("2026-08-12 16:20:00"), "3F-Color-01", 3, 1,
         "scan001.pdf", ACCOUNT, "Acrobat", "일반"],
    ])

    _sheet(wb, "DLP Email Log", DLP_HEADER, [
        # 같은 메일이 규칙 2개로 인시던트 2건 → 60초 이내라 1건으로 병합, 서명 이미지는 제외
        ["INC-1001", "2026-08-27 21:00:00", ACCOUNT, "자료 공유", f"{ACCOUNT}@doosan.com", "friend@gmail.com",
         "임직원 연락처.xlsx", 45_000, "개인정보", "Allowed", None, "Outlook"],
        ["INC-1001", "2026-08-27 21:00:00", ACCOUNT, "자료 공유", f"{ACCOUNT}@doosan.com", "friend@gmail.com",
         "image001.png", 3_000, "개인정보", "Allowed", None, "Outlook"],
        ["INC-1002", "2026-08-27 21:00:30", ACCOUNT, "자료 공유", f"{ACCOUNT}@doosan.com", "friend@gmail.com",
         "임직원 연락처.xlsx", 45_000, "대용량 첨부", "Allowed", None, "Outlook"],
        # 개인메일 + 사내 동반 수신 → R2
        ["INC-1003", "2026-06-15 10:00:00", ACCOUNT, "미팅 자료", f"{ACCOUNT}@doosan.com", "partner@vendor.co.kr",
         "회의록.docx", 20_000, "외부 발송", "Allowed", None, "Outlook"],
        ["INC-1003", "2026-06-15 10:00:00", ACCOUNT, "미팅 자료", f"{ACCOUNT}@doosan.com", "me@naver.com",
         "회의록.docx", 20_000, "외부 발송", "Allowed", None, "Outlook"],
        ["INC-1003", "2026-06-15 10:00:00", ACCOUNT, "미팅 자료", f"{ACCOUNT}@doosan.com", "colleague@doosan.com",
         "회의록.docx", 20_000, "외부 발송", "Allowed", None, "Outlook"],
        # 공공기관 → R3
        ["INC-1004", "2026-08-28 10:00:00", ACCOUNT, "협조 요청", f"{ACCOUNT}@doosan.com", "officer@korea.go.kr",
         "협조요청.hwp", 15_000, "외부 발송", "Allowed", None, "Outlook"],
        # 차단 → R1
        ["INC-1005", "2026-07-10 11:00:00", ACCOUNT, "개인 백업", f"{ACCOUNT}@doosan.com", "x@gmail.com",
         "고객목록.xlsx", 90_000, "개인정보", "Blocked", None, "Outlook"],
        # REQ-11 타사 + 사내 동반 → R2 참고 (자료 키워드·대용량이어도 B3·B10 안 붙음)
        ["INC-1007", "2026-08-20 10:00:00", ACCOUNT, "도면 검토 요청", f"{ACCOUNT}@doosan.com", "partner@vendor.co.kr",
         "도면_검토요청.pdf", 150 * MB, "외부 발송", "Allowed", None, "Outlook"],
        ["INC-1007", "2026-08-20 10:00:00", ACCOUNT, "도면 검토 요청", f"{ACCOUNT}@doosan.com", "colleague@doosan.com",
         "도면_검토요청.pdf", 150 * MB, "외부 발송", "Allowed", None, "Outlook"],
        # REQ-11 개인메일 + 발신자 본인 사내 주소만 → 동반 아님, B2
        ["INC-1008", "2026-08-28 20:00:00", ACCOUNT, "개인 보관", f"{ACCOUNT}@doosan.com", "me@gmail.com",
         "개인자료_정리.xlsx", 30_000, "개인정보", "Allowed", None, "Outlook"],
        ["INC-1008", "2026-08-28 20:00:00", ACCOUNT, "개인 보관", f"{ACCOUNT}@doosan.com", f"{ACCOUNT}@doosan.com",
         "개인자료_정리.xlsx", 30_000, "개인정보", "Allowed", None, "Outlook"],
        # REQ-11 보안 키워드 + 사내 동반 → B1 확인 필요 유지 + R2, 사건의 행위 맥락은 낮음
        ["INC-1009", "2026-08-22 11:00:00", ACCOUNT, "견적 회신", f"{ACCOUNT}@doosan.com", "buyer@client.co.kr",
         "대외비_견적.pdf", 80_000, "외부 발송", "Allowed", None, "Outlook"],
        ["INC-1009", "2026-08-22 11:00:00", ACCOUNT, "견적 회신", f"{ACCOUNT}@doosan.com", "team.lead@hq.doosan.com",
         "대외비_견적.pdf", 80_000, "외부 발송", "Allowed", None, "Outlook"],
        # 첨부 없는 개인메일 → 개인메일 발송 영역 아래쪽
        ["INC-1006", "2026-08-30 09:00:00", ACCOUNT, "안부", f"{ACCOUNT}@doosan.com", "me2@daum.net",
         None, None, "외부 발송", "Allowed", None, "Outlook"],
    ])

    _sheet(wb, "DLP Web Log", DLP_HEADER, [
        ["INC-2001", "2026-08-29 20:15:00", ACCOUNT, None, None, "https://www.dropbox.com/upload",
         "Plan_2027.pptx", 3_500_000, "클라우드 업로드", "Allowed", None, "chrome.exe"],
        ["INC-2002", "2026-08-05 15:00:00", ACCOUNT, None, None, "https://acme.agiloft.com/ui",
         "계약서_검토본.pdf", 400_000, "외부 업로드", "Allowed", None, "chrome.exe"],
        ["INC-2003", "2026-08-31 22:00:00", ACCOUNT, None, None, "https://wetransfer.com/upload",
         "프로젝트_자료.zip", 100 * MB, "클라우드 업로드", "Allowed", None, "chrome.exe"],
        # 개인메일 사이트로 파일 업로드 → 개인메일 발송 영역
        ["INC-2004", "2026-08-30 21:30:00", ACCOUNT, None, None, "https://mail.naver.com/upload",
         "견적서.pdf", 250_000, "외부 업로드", "Allowed", None, "chrome.exe"],
    ])

    _sheet(wb, "DLP USB Log", DLP_HEADER, [
        ["INC-3001", "2026-08-25 19:40:30", ACCOUNT, None, None, None,
         "설계도면_v2.dwg", 2_048_000, "USB 반출", "Allowed", "E:\\설계도면_v2.dwg", "explorer.exe"],
    ])

    wb.save(path)
    return path
