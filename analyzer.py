"""
분석 엔진: Pass 1 (개별 분석) + Pass 2 (종합 합성)
"""
import asyncio
import json
import logging
from datetime import datetime

from config import MODEL_NAME_PASS1, MODEL_NAME_PASS2
from prompt import (
    PASS1_PRICE_SYSTEM, PASS1_PRICE_USER,
    PASS1_NEWS_SYSTEM, PASS1_NEWS_USER,
    PASS1_DISCLOSURE_SYSTEM, PASS1_DISCLOSURE_USER,
    PASS2_SYSTEM, PASS2_USER,
)
from utils import call_llm, format_json

logger = logging.getLogger("stock_analyst.analyzer")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pass 1: 개별 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def analyze_price(ticker: str, company_name: str, signal_data: str) -> dict | None:
    """Pass 1-A: 가격 시그널 기술적 분석."""
    user_prompt = PASS1_PRICE_USER.format(
        ticker=ticker,
        company_name=company_name,
        asset_signal=signal_data,
    )
    logger.info("[Pass1-A] 가격 분석 시작: %s", ticker)
    result = await call_llm(PASS1_PRICE_SYSTEM, user_prompt, model=MODEL_NAME_PASS1)
    if result:
        logger.info("[Pass1-A] 가격 분석 완료: %s", ticker)
    else:
        logger.warning("[Pass1-A] 가격 분석 실패: %s", ticker)
    return result


async def analyze_news(ticker: str, company_name: str, news_text: str) -> dict | None:
    """Pass 1-B: 뉴스 센티먼트 분석."""
    user_prompt = PASS1_NEWS_USER.format(
        ticker=ticker,
        company_name=company_name,
        news_articles=news_text,
    )
    logger.info("[Pass1-B] 뉴스 분석 시작: %s", ticker)
    result = await call_llm(PASS1_NEWS_SYSTEM, user_prompt, model=MODEL_NAME_PASS1)
    if result:
        logger.info("[Pass1-B] 뉴스 분석 완료: %s", ticker)
    else:
        logger.warning("[Pass1-B] 뉴스 분석 실패: %s", ticker)
    return result


async def analyze_disclosure(ticker: str, company_name: str, disclosure_text: str) -> dict | None:
    """Pass 1-C: 공시 분석."""
    user_prompt = PASS1_DISCLOSURE_USER.format(
        ticker=ticker,
        company_name=company_name,
        disclosure_summary=disclosure_text,
    )
    logger.info("[Pass1-C] 공시 분석 시작: %s", ticker)
    result = await call_llm(PASS1_DISCLOSURE_SYSTEM, user_prompt, model=MODEL_NAME_PASS1)
    if result:
        logger.info("[Pass1-C] 공시 분석 완료: %s", ticker)
    else:
        logger.warning("[Pass1-C] 공시 분석 실패: %s", ticker)
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pass 1: 병렬 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FAILED_PLACEHOLDER = {
    "error": "해당 소스 분석 실패 — 데이터 수집 또는 LLM 응답 오류"
}


async def run_pass1(
    ticker: str,
    company_name: str,
    signal_data: str,
    news_text: str,
    disclosure_text: str,
) -> tuple[dict, dict, dict]:
    """
    Pass 1 세 분석을 병렬로 실행한다.
    개별 실패 시 FAILED_PLACEHOLDER를 넣어 Pass 2가 중단되지 않도록 한다.
    """
    results = await asyncio.gather(
        analyze_price(ticker, company_name, signal_data),
        analyze_news(ticker, company_name, news_text),
        analyze_disclosure(ticker, company_name, disclosure_text),
        return_exceptions=True,
    )

    parsed = []
    labels = ["가격", "뉴스", "공시"]
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error("[Pass1] %s 분석 예외: %s", labels[i], r)
            parsed.append(FAILED_PLACEHOLDER)
        elif r is None:
            parsed.append(FAILED_PLACEHOLDER)
        else:
            parsed.append(r)

    return parsed[0], parsed[1], parsed[2]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pass 2: 종합 합성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def synthesize(
    ticker: str,
    company_name: str,
    pass1_price: dict,
    pass1_news: dict,
    pass1_disclosure: dict,
    analysis_date: str | None = None,
) -> dict | None:
    """
    Pass 1 결과 3개를 종합하여 최종 분석 JSON을 생성한다.
    """
    if analysis_date is None:
        analysis_date = datetime.now().strftime("%Y-%m-%d")

    user_prompt = PASS2_USER.format(
        ticker=ticker,
        company_name=company_name,
        analysis_date=analysis_date,
        pass1_price=format_json(pass1_price),
        pass1_news=format_json(pass1_news),
        pass1_disclosure=format_json(pass1_disclosure),
    )

    logger.info("[Pass2] 종합 합성 시작: %s", ticker)
    result = await call_llm(PASS2_SYSTEM, user_prompt, model=MODEL_NAME_PASS2)

    if result:
        logger.info("[Pass2] 종합 합성 완료: %s", ticker)
        # 최종 출력 구조 검증
        if not _validate_final_output(result):
            logger.warning("[Pass2] 출력 구조 불일치 — 그대로 반환")
    else:
        logger.error("[Pass2] 종합 합성 실패: %s", ticker)

    return result


def _validate_final_output(data: dict) -> bool:
    """최종 JSON 구조가 기대 형식과 일치하는지 검증한다."""
    try:
        assert "synthesis" in data, "synthesis 키 누락"
        assert "outlook" in data, "outlook 키 누락"

        syn = data["synthesis"]
        assert all(k in syn for k in ("advantage", "risk", "viewpoint")), \
            "synthesis 하위 키 누락"

        out = data["outlook"]
        assert all(k in out for k in ("short", "mid", "long")), \
            "outlook 하위 키 누락"

        return True
    except AssertionError as e:
        logger.warning("출력 검증 실패: %s", e)
        return False
