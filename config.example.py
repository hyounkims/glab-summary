"""
민감 설정값 예시 파일
사용법: 이 파일을 복사하여 config.py로 이름을 변경한 뒤 실제 값을 입력하세요.
    cp config.example.py config.py
"""

# ── API 설정 ──────────────────────────────────────────────
OPENAI_API_KEY = "sk-proj-YOUR_OPENAI_API_KEY"
MODEL_NAME_PASS1 = "gpt-4.1-mini"   # Pass 1 (개별 분석) — 경량 모델
MODEL_NAME_PASS2 = "gpt-5.1"        # Pass 2 (종합 합성) — 고품질 모델
MAX_TOKENS = 2000
TEMPERATURE = 0.3

# ── 뉴스 설정 ─────────────────────────────────────────────
NEWS_MAX_ARTICLES = 40
NEWS_SEARCH_KEYWORDS = ["주가", "실적", "주식", "공시"]
NEWS_MAX_BODY_LENGTH = 1000

# ── 공시 설정 ─────────────────────────────────────────────
DISCLOSURE_MAX_COUNT = 20

# ── Naver API 설정 ────────────────────────────────────────
NAVER_CLIENT_ID = "YOUR_NAVER_CLIENT_ID"
NAVER_CLIENT_SECRET = "YOUR_NAVER_CLIENT_SECRET"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

# ── DART API 설정 (미사용 — 코드 보존용) ──────────────────
DART_API_KEY = "YOUR_DART_API_KEY"
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"

# ── DB 설정 ───────────────────────────────────────────────
DB_HOST = "your-db-host.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "your_db_name"
DB_USER = "your_db_user"
DB_PASSWORD = "your_db_password"
