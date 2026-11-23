"""
로컬 설정 파일(obi.ini) 읽기 — 접속 비밀번호 · AI 분석 엔진 설정

- 위치: 환경변수 OBI_CONFIG_PATH, 없으면 이 폴더의 obi.ini
- obi.ini 는 PC마다 따로 만들고 커밋하지 않는다 (.gitignore). 견본은 obi.ini.example
- 파일이 없거나 비밀번호가 4자리 숫자가 아니면 앱은 메인 화면을 열지 않는다 (fail closed)
"""
import configparser
import hmac
import os

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE_PATH = os.path.join(HERE, "obi.ini.example")

DEFAULTS = {
    "ollama_base_url": "http://localhost:11434",
    "ollama_models":   ["gemma4:31b", "qwen3.8:27b"],
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model":    "gpt-4o-mini",
    "openai_api_key":  "",
}


def config_path():
    return os.environ.get("OBI_CONFIG_PATH") or os.path.join(HERE, "obi.ini")


def _split(value):
    return [x.strip() for x in value.replace("\n", ",").split(",") if x.strip()]


def load():
    """설정을 dict 로 돌려준다. 오류는 '오류' 키에 문장으로 담는다 (예외를 던지지 않음)."""
    path = config_path()
    cfg = dict(DEFAULTS, password="", 경로=path, 오류="")
    if not os.path.exists(path):
        cfg["오류"] = (f"설정 파일이 없습니다: {path}\n"
                      f"obi.ini.example 을 obi.ini 로 복사하고 [auth] password 에 4자리 숫자를 넣으세요.")
        return cfg
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8-sig") as f:
            cp.read_file(f)
    except (configparser.Error, UnicodeDecodeError) as e:
        cfg["오류"] = f"설정 파일을 읽을 수 없습니다: {path}\n{e}"
        return cfg

    cfg["password"] = cp.get("auth", "password", fallback="").strip()
    cfg["ollama_base_url"] = cp.get("ollama", "base_url", fallback=DEFAULTS["ollama_base_url"]).strip()
    if cp.has_option("ollama", "models"):
        cfg["ollama_models"] = _split(cp.get("ollama", "models"))
    cfg["openai_base_url"] = cp.get("openai", "base_url", fallback=DEFAULTS["openai_base_url"]).strip()
    cfg["openai_model"] = cp.get("openai", "model", fallback=DEFAULTS["openai_model"]).strip()
    cfg["openai_api_key"] = cp.get("openai", "api_key", fallback="").strip()

    if not (len(cfg["password"]) == 4 and cfg["password"].isdigit()):
        cfg["오류"] = f"{path} 의 [auth] password 는 4자리 숫자여야 합니다."
    return cfg


def check_password(cfg, entered):
    """설정이 올바르고 입력이 일치할 때만 True (상수 시간 비교)"""
    if cfg.get("오류") or not cfg.get("password"):
        return False
    return hmac.compare_digest(str(entered).strip().encode(), cfg["password"].encode())
