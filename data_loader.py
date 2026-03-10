"""
데이터 수집: DB(가격 시그널), 네이버 뉴스, DB(공시 요약)
각 함수는 str을 반환하여 프롬프트에 바로 주입할 수 있도록 한다.
"""
import asyncio
import json
import re
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras
import httpx
from bs4 import BeautifulSoup

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, NAVER_NEWS_URL,
    NEWS_MAX_ARTICLES, NEWS_SEARCH_KEYWORDS, NEWS_MAX_BODY_LENGTH,
    DISCLOSURE_MAX_COUNT,
)
from utils import truncate

logger = logging.getLogger("stock_analyst.data_loader")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DB 연결 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode="require",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1) 가격 시그널 — DB에서 asset_signal 최신 1행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def load_asset_signal(ticker: str) -> str:
    """
    DB에서 해당 ticker(isu_short_code)의 asset_signal 최신 레코드를 가져온다.
    반환: JSON 문자열 (프롬프트 주입용)
    """
    def _query():
        conn = _get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT *
            FROM stock.asset_signal
            WHERE isu_short_code = %s
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (ticker,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row

    try:
        row = await asyncio.to_thread(_query)

        if row is None:
            logger.warning("asset_signal 데이터 없음: %s", ticker)
            return json.dumps({"error": "가격 시그널 데이터 없음"}, ensure_ascii=False)

        data = dict(row)
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()

        return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error("asset_signal 로드 실패: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2) 네이버 뉴스 검색 + 본문 크롤링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def load_news(ticker: str, company_name: str = "") -> str:
    """
    네이버 뉴스에서 종목명으로 4가지 키워드 검색 → 본문 수집.
    반환: 기사별 텍스트 블록 (프롬프트 주입용)
    """
    search_name = company_name or ticker

    try:
        articles = await _search_naver_news(search_name)
    except Exception as e:
        logger.error("뉴스 검색 실패: %s", e)
        return "(뉴스 데이터 수집 실패)"

    if not articles:
        return "(관련 뉴스 없음)"

    logger.info("뉴스 검색 완료: %d건", len(articles))

    # 본문 순차 수집 (httpx AsyncClient 재사용)
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        bodies = []
        for art in articles:
            body = await _fetch_article_body(client, art.get("link", ""))
            bodies.append(body)

    # 프롬프트용 텍스트 조합
    parts = []
    for i, (art, body) in enumerate(zip(articles, bodies), 1):
        body_text = truncate(body, NEWS_MAX_BODY_LENGTH) if body else "(본문 수집 실패)"
        parts.append(
            f"---기사 {i}---\n"
            f"제목: {art.get('title', '제목 없음')}\n"
            f"날짜: {art.get('date', '날짜 없음')}\n"
            f"본문:\n{body_text}"
        )

    return "\n\n".join(parts)


async def _search_naver_news(company_name: str) -> list[dict]:
    """
    네이버 뉴스 검색 API — 4가지 키워드로 검색 후 URL 중복 제거.
    반환: [{"title": ..., "link": ..., "date": ...}, ...]
    """
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    per_keyword = max(1, NEWS_MAX_ARTICLES // len(NEWS_SEARCH_KEYWORDS))

    all_items = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(timeout=10) as client:
        for keyword in NEWS_SEARCH_KEYWORDS:
            query = f"{company_name} {keyword}"
            params = {
                "query": query,
                "display": per_keyword,
                "sort": "date",
            }
            try:
                resp = await client.get(NAVER_NEWS_URL, headers=headers, params=params)
                resp.raise_for_status()
                items = resp.json().get("items", [])
                for item in items:
                    link = item.get("link", "")
                    if link and link not in seen_urls:
                        seen_urls.add(link)
                        all_items.append({
                            "title": _strip_html(item.get("title", "")),
                            "link": link,
                            "date": item.get("pubDate", ""),
                        })
                logger.debug("[%s] %d건 수집", query, len(items))
            except Exception as e:
                logger.warning("키워드 검색 실패 (%s): %s", query, e)

    return all_items[:NEWS_MAX_ARTICLES]


async def _fetch_article_body(client: httpx.AsyncClient, url: str) -> str:
    """
    뉴스 URL에서 본문 텍스트를 추출한다.
    네이버 뉴스 선택자 4단계 fallback 적용.
    """
    if not url:
        return ""

    try:
        resp = await client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # 1. 네이버 뉴스 표준 본문
        article = soup.find("article", {"id": "dic_area"})
        # 2. 다른 구조의 네이버 뉴스
        if not article:
            article = soup.find("div", {"id": "newsEndContents"})
        # 3. 일반 article 태그
        if not article:
            article = soup.find("article")
        # 4. 본문 class 패턴 매칭
        if not article:
            article = soup.find("div", {"class": re.compile(r"(article|content|body|text)")})

        if article:
            return article.get_text(separator="\n", strip=True)
        return ""

    except Exception as e:
        logger.warning("본문 수집 실패 (%s): %s", url[:60], e)
        return ""


def _strip_html(text: str) -> str:
    """HTML 태그 제거."""
    return re.sub(r"<[^>]+>", "", text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3) 공시 요약 — DB stock_disclosure 기반
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def load_disclosure(ticker: str, corp_code: str = "") -> str:
    """
    DB의 stock.stock_disclosure 테이블에서 해당 종목의 공시 요약을 가져온다.
    최신순으로 DISCLOSURE_MAX_COUNT건 조회.
    반환: 공시 요약 텍스트 (프롬프트 주입용)
    """
    def _query():
        conn = _get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT title, receipt_date, summary
            FROM stock.stock_disclosure
            WHERE isu_short_code = %s
            ORDER BY receipt_date DESC
            LIMIT %s
            """,
            (ticker, DISCLOSURE_MAX_COUNT),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    try:
        rows = await asyncio.to_thread(_query)

        if not rows:
            logger.warning("공시 데이터 없음: %s", ticker)
            return "(공시 데이터 없음)"

        logger.info("공시 조회 완료: %d건", len(rows))

        parts = []
        for r in rows:
            summary = r["summary"] or ""
            parts.append(f"[{r['receipt_date']}] {r['title']}\n{summary}")

        return "\n\n".join(parts)

    except Exception as e:
        logger.error("공시 로드 실패: %s", e)
        return f"(공시 데이터 수집 실패: {e})"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# (미사용) DART API 직접 호출 — 추후 필요 시 활성화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fetch_dart_disclosure(corp_code: str, days: int = 90) -> str:
    """
    DART API list.json에서 최근 공시를 직접 조회한다.
    현재 미사용 — load_disclosure()는 DB 기반으로 동작.
    """
    import zipfile
    import io
    import xml.etree.ElementTree as ET
    from datetime import timedelta
    from config import DART_API_KEY, DART_LIST_URL

    if not corp_code:
        return "(corp_code 미제공)"

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": start_date,
        "end_de": end_date,
        "page_count": 10,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(DART_LIST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("list", [])
        if not items:
            return "(최근 공시 없음)"

        parts = [
            f"- [{item.get('rcept_dt', '')}] {item.get('report_nm', '')} | {item.get('flr_nm', '')}"
            for item in items
        ]
        return "\n".join(parts)

    except Exception as e:
        logger.error("DART API 호출 실패: %s", e)
        return f"(DART API 오류: {e})"
