"""
공통 유틸리티: LLM 호출, JSON 안전 파싱, 재시도 로직, 로깅
"""
import json
import re
import logging
import asyncio
from typing import Any

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, MODEL_NAME_PASS2, MAX_TOKENS, TEMPERATURE

logger = logging.getLogger("stock_analyst")

_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# 토큰 사용량 누적 로그 (호출마다 append)
_token_log: list[dict] = []


def get_token_log() -> list[dict]:
    return list(_token_log)


def reset_token_log() -> None:
    _token_log.clear()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# JSON 안전 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def safe_parse_json(text: str) -> dict | None:
    """
    LLM 응답에서 JSON을 안전하게 추출/파싱한다.
    - markdown 코드블록 제거
    - 앞뒤 불필요한 텍스트 제거
    - 파싱 실패 시 None 반환
    """
    if not text:
        return None

    # markdown 코드블록 제거
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()

    # JSON 객체 영역만 추출 (첫 { 부터 마지막 } 까지)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        logger.warning("JSON 객체를 찾을 수 없음: %s...", text[:100])
        return None

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        logger.warning("JSON 파싱 실패: %s | 원문: %s...", e, text[:200])
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 호출 (비동기) — openai Responses API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> dict | None:
    """
    OpenAI Responses API를 호출하고 JSON 응답을 파싱하여 반환한다.
    - client.responses.create() 사용
    - 재시도 로직 포함
    - JSON 파싱 실패 시 None 반환
    """
    model = model or MODEL_NAME_PASS2

    for attempt in range(1, max_retries + 1):
        try:
            response = await _openai_client.responses.create(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
            )
            raw_text = response.output_text

            # 토큰 사용량 기록
            usage = response.usage
            _token_log.append({
                "model": model,
                "input": usage.input_tokens,
                "output": usage.output_tokens,
            })

            parsed = safe_parse_json(raw_text)

            if parsed is not None:
                return parsed

            logger.warning(
                "LLM 응답 JSON 파싱 실패 (시도 %d/%d)",
                attempt, max_retries,
            )

        except Exception as e:
            logger.error(
                "LLM 호출 오류 (시도 %d/%d): %s",
                attempt, max_retries, e,
            )

        if attempt < max_retries:
            await asyncio.sleep(retry_delay * attempt)

    logger.error("LLM 호출 최종 실패 (max_retries=%d)", max_retries)
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기타 유틸
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def truncate(text: str, max_length: int) -> str:
    """텍스트를 max_length 글자로 자르고 말줄임 표시를 붙인다."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def format_json(data: Any, ensure_ascii: bool = False) -> str:
    """dict를 보기 좋은 JSON 문자열로 변환한다."""
    return json.dumps(data, ensure_ascii=ensure_ascii, indent=2)


def setup_logging(level: int = logging.INFO) -> None:
    """로깅 설정."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
