# glab-summary

한국 주식 종목 AI 분석 파이프라인.
네이버 뉴스, DART 공시, 가격 시그널 데이터를 수집하여 2-Pass LLM 분석을 수행하고 결과를 DB에 저장한다.

---

## 아키텍처

```
[데이터 수집] ─────────────────────────────────────────────
  asset_signal (DB)   →  가격/기술적 시그널
  Naver News API      →  최신 뉴스 4키워드 검색 + 본문 스크랩
  stock_disclosure(DB)→  공시 요약 (최대 20건)

        ↓ 병렬 수집

[Pass 1 — 병렬 개별 분석] (gpt-4o-mini)
  ├── 가격 시그널 분석  →  추세/모멘텀/지지저항
  ├── 뉴스 센티먼트 분석 → 핵심 테마/촉매/우려사항
  └── 공시 분석        →  주요 공시/재무영향/자본이벤트

        ↓

[Pass 2 — 종합 합성] (gpt-4o)
  └── 최종 투자 분석   →  synthesis(강점/리스크/시각) + outlook(단기/중기/장기)

        ↓

[DB 저장]
  ├── stock.stock_synthesis      (advantage / risk / viewpoint)
  └── stock.analyze_disclosure   (short_term / mid_term / long_term)
```

---

## 파일 구조

```
glab-summary/
├── config.py         # API 설정, DB 접속 정보, 프롬프트 템플릿
├── data_loader.py    # 데이터 수집 (가격 시그널, 뉴스, 공시)
├── analyzer.py       # Pass 1 / Pass 2 LLM 분석 엔진
├── db_writer.py      # DB 저장 및 조회 (분석 결과, 미분석 종목)
├── utils.py          # LLM 호출, JSON 파싱, 로깅 유틸
├── main.py           # CLI 진입점 (단일 / 배치)
└── requirements.txt  # 의존성 패키지
```

### config.py

| 설정 | 설명 | 기본값 |
|------|------|--------|
| `MODEL_NAME_PASS1` | Pass 1 분석 모델 (경량) | `gpt-4o-mini` |
| `MODEL_NAME_PASS2` | Pass 2 합성 모델 (고품질) | `gpt-4o` |
| `NEWS_MAX_ARTICLES` | 뉴스 최대 수집 건수 | `40` |
| `NEWS_SEARCH_KEYWORDS` | 뉴스 검색 키워드 조합 | `주가/실적/주식/공시` |
| `NEWS_MAX_BODY_LENGTH` | 기사 본문 최대 글자수 | `1000` |
| `DISCLOSURE_MAX_COUNT` | 공시 DB 최대 조회 건수 | `20` |

### data_loader.py

| 함수 | 설명 |
|------|------|
| `load_asset_signal(ticker)` | `stock.asset_signal` 테이블에서 최신 가격 시그널 조회 |
| `load_news(ticker, company_name)` | 네이버 뉴스 4키워드 검색 + BeautifulSoup 본문 스크랩 |
| `load_disclosure(ticker, corp_code)` | `stock.stock_disclosure` 테이블에서 공시 요약 조회 |
| `_fetch_dart_disclosure(corp_code)` | DART API 직접 호출 (미사용 — 필요 시 활성화) |

### db_writer.py

| 함수 | 설명 |
|------|------|
| `save_result(ticker, synthesis, outlook)` | 분석 결과를 `stock_synthesis` / `analyze_disclosure` 테이블에 저장 |
| `already_analyzed(ticker)` | 해당 종목의 분석 결과 존재 여부 확인 |
| `fetch_unanalyzed_stocks(limit)` | 공시는 있으나 분석 결과가 없는 종목 조회 |
| `get_stock_info(ticker)` | `stock.stock` 테이블에서 종목명 및 corp_code 조회 |

---

## DB 테이블

| 테이블 | 용도 |
|--------|------|
| `stock.asset_signal` | 가격/기술적 시그널 입력 데이터 |
| `stock.stock_disclosure` | 공시 요약 입력 데이터 (`title`, `receipt_date`, `summary`) |
| `stock.stock` | 종목 기본 정보 (`isu_short_code`, `corp_code`, `description`) |
| `stock.stock_synthesis` | 분석 결과 저장 — insight_type: `advantage` / `risk` / `viewpoint` |
| `stock.analyze_disclosure` | 분석 결과 저장 — horizon: `short_term` / `mid_term` / `long_term` |

---

## 설치

```bash
pip install -r requirements.txt
```

---

## 실행

### 단일 종목 분석

```bash
python main.py --ticker 005930 --name 삼성전자
```

결과를 파일로 저장:

```bash
python main.py --ticker 005930 --name 삼성전자 --output result.json
```

분석 결과를 DB에 저장:

```bash
python main.py --ticker 005930 --name 삼성전자 --save
```

### 배치 분석 (미분석 종목 자동 처리)

```bash
# 미분석 종목 10개 처리
python main.py --batch 10

# 미분석 종목 50개 처리 (디버그 로그 포함)
python main.py --batch 50 --debug
```

배치 모드 동작:
1. `stock_disclosure`에 공시가 있고 `analyze_disclosure`에 결과가 없는 종목을 자동 선택
2. `stock.stock` 테이블에서 종목명과 corp_code를 자동으로 조회
3. 이미 분석된 종목은 건너뜀

### 옵션 전체 목록

| 옵션 | 설명 |
|------|------|
| `--ticker` | 종목 코드 (단일 분석 시 필수) |
| `--name` | 종목명 (`--ticker` 사용 시 필수) |
| `--batch N` | 배치 분석 — 미분석 종목 N개 처리 |
| `--corp-code` | DART 고유번호 (생략 시 DB에서 자동 조회) |
| `--output` | 결과 JSON 저장 경로 |
| `--save` | 분석 결과를 DB에 저장 (기본: print만) |
| `--debug` | 디버그 로그 활성화 |

> `--ticker`와 `--batch`는 동시에 사용할 수 없다.

---

## 출력 구조 (JSON)

```json
{
  "meta": {
    "ticker": "005930",
    "company_name": "삼성전자",
    "analysis_date": "2026-03-10",
    "model_pass1": "gpt-4o-mini",
    "model_pass2": "gpt-4o",
    "elapsed_sec": 12.3
  },
  "pass1": {
    "price": { "trend": "...", "signal_summary": "...", "momentum": "..." },
    "news":  { "sentiment": "...", "key_themes": ["..."], "catalysts": "..." },
    "disclosure": { "key_filings": ["..."], "impact_assessment": "..." }
  },
  "result": {
    "synthesis": {
      "advantage": "...",
      "risk": "...",
      "viewpoint": "..."
    },
    "outlook": {
      "short": "...",
      "mid": "...",
      "long": "..."
    }
  }
}
```
