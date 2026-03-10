"""
주식 종목 분석 파이프라인 진입점

사용법:
    # 단일 종목
    python main.py --ticker 005930 --name 삼성전자
    python main.py --ticker 005930 --name 삼성전자 --output result.json

    # 배치 처리 (미분석 종목 자동 선택)
    python main.py --batch 10
    python main.py --batch 50 --debug
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from data_loader import load_asset_signal, load_news, load_disclosure
from analyzer import run_pass1, synthesize
from db_writer import save_result, already_analyzed, fetch_unanalyzed_stocks, get_stock_info
from utils import setup_logging, format_json, get_token_log, reset_token_log
import config

logger = logging.getLogger("stock_analyst")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 단일 종목 분석 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run(
    ticker: str,
    company_name: str,
    corp_code: str = "",
    output_path: str | None = None,
    save_db: bool = False,
) -> dict | None:
    """
    전체 분석 파이프라인 실행.

    1단계: 데이터 수집 (병렬)
    2단계: Pass 1 — 개별 분석 (병렬)
    3단계: Pass 2 — 종합 합성
    4단계: DB 저장
    """
    start = datetime.now()
    analysis_date = start.strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("분석 시작: %s (%s) | 기준일: %s", ticker, company_name, analysis_date)
    logger.info("=" * 60)

    # ── 1단계: 데이터 수집 (병렬) ──────────────────────────
    logger.info("[1/4] 데이터 수집 시작")
    signal_data, news_text, disclosure_text = await asyncio.gather(
        load_asset_signal(ticker),
        load_news(ticker, company_name),
        load_disclosure(ticker, corp_code),
    )
    logger.info("[1/4] 데이터 수집 완료")

    # ── 2단계: Pass 1 — 개별 분석 (병렬) ──────────────────
    logger.info("[2/4] Pass 1 개별 분석 시작 (모델: %s)", config.MODEL_NAME_PASS1)
    p1_price, p1_news, p1_disclosure = await run_pass1(
        ticker, company_name,
        signal_data, news_text, disclosure_text,
    )
    logger.info("[2/4] Pass 1 개별 분석 완료")

    # ── 3단계: Pass 2 — 종합 합성 ─────────────────────────
    logger.info("[3/4] Pass 2 종합 합성 시작 (모델: %s)", config.MODEL_NAME_PASS2)
    result = await synthesize(
        ticker, company_name,
        p1_price, p1_news, p1_disclosure,
        analysis_date=analysis_date,
    )

    if result is None:
        logger.error("최종 분석 결과 생성 실패: %s", ticker)
        return None

    logger.info("[3/4] Pass 2 종합 합성 완료")

    # ── 4단계: DB 저장 ─────────────────────────────────────
    if save_db:
        logger.info("[4/4] DB 저장 시작")
        await save_result(ticker, result.get("synthesis", {}), result.get("outlook", {}))
        logger.info("[4/4] DB 저장 완료")
    else:
        logger.info("[4/4] DB 저장 건너뜀 (--save 플래그 없음)")

    # ── 결과 구성 ──────────────────────────────────────────
    elapsed = (datetime.now() - start).total_seconds()
    final_output = {
        "meta": {
            "ticker": ticker,
            "company_name": company_name,
            "analysis_date": analysis_date,
            "model_pass1": config.MODEL_NAME_PASS1,
            "model_pass2": config.MODEL_NAME_PASS2,
            "elapsed_sec": round(elapsed, 1),
        },
        "pass1": {
            "price": p1_price,
            "news": p1_news,
            "disclosure": p1_disclosure,
        },
        "result": result,
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)
        logger.info("결과 저장: %s", output_path)
    else:
        print("\n" + "=" * 60)
        print("최종 분석 결과")
        print("=" * 60)
        print(format_json(final_output))

    # ── 토큰 사용량 출력 ──────────────────────────────────
    _print_token_summary()

    logger.info("완료: %s (소요: %.1f초)", ticker, elapsed)
    return final_output


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 배치 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _print_token_summary() -> None:
    """모델별 토큰 사용량 집계 및 출력."""
    logs = get_token_log()
    if not logs:
        return

    # 모델별 집계
    summary: dict[str, dict] = {}
    for entry in logs:
        m = entry["model"]
        if m not in summary:
            summary[m] = {"calls": 0, "input": 0, "output": 0}
        summary[m]["calls"] += 1
        summary[m]["input"] += entry["input"]
        summary[m]["output"] += entry["output"]

    total_input = sum(v["input"] for v in summary.values())
    total_output = sum(v["output"] for v in summary.values())

    print("\n" + "─" * 50)
    print("토큰 사용량")
    print("─" * 50)
    for model, s in summary.items():
        print(f"  {model} (호출 {s['calls']}회)")
        print(f"    input:  {s['input']:,}")
        print(f"    output: {s['output']:,}")
        print(f"    합계:   {s['input'] + s['output']:,}")
    print("─" * 50)
    print(f"  전체 합계  input: {total_input:,} / output: {total_output:,} / total: {total_input + total_output:,}")
    print("─" * 50)


async def run_batch(limit: int, save_db: bool = False) -> None:
    """
    미분석 종목을 limit개 가져와 순차 분석한다.
    이미 분석된 종목은 건너뜀.
    """
    logger.info("배치 시작: 미분석 종목 최대 %d개", limit)
    tickers = await fetch_unanalyzed_stocks(limit)

    if not tickers:
        logger.info("미분석 종목 없음")
        return

    logger.info("대상 종목 %d개: %s", len(tickers), tickers)
    print(f"\n미분석 대상 종목 {len(tickers)}개: {tickers}\n{'='*50}\n")

    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] {'='*40}")

        if await already_analyzed(ticker):
            logger.info("%s → 이미 분석 완료, 건너뜀", ticker)
            print(f"{ticker} → 이미 분석 완료, 건너뜀")
            continue

        # stock 테이블에서 종목명/corp_code 조회
        info = await get_stock_info(ticker)
        company_name = info["company_name"]
        corp_code = info["corp_code"]

        logger.info("[%d/%d] 분석 시작: %s (%s)", i, len(tickers), ticker, company_name)
        reset_token_log()
        await run(ticker, company_name, corp_code=corp_code, save_db=save_db)

    logger.info("배치 완료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    parser = argparse.ArgumentParser(description="주식 종목 AI 분석기")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ticker", help="종목 코드 (단일 분석, 예: 005930)")
    mode.add_argument("--batch", type=int, metavar="N", help="배치 분석: 미분석 종목 N개")

    parser.add_argument("--name", help="종목명 (생략 시 DB에서 자동 조회, 예: 삼성전자)")
    parser.add_argument("--corp-code", default="", help="DART 고유번호 (예: 00126380)")
    parser.add_argument("--output", default=None, help="결과 저장 경로 (예: result.json)")
    parser.add_argument("--save", action="store_true", help="분석 결과를 DB에 저장")
    parser.add_argument("--debug", action="store_true", help="디버그 로깅 활성화")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_level)

    if args.batch:
        asyncio.run(run_batch(args.batch, save_db=args.save))

    else:
        if args.name:
            company_name = args.name
            corp_code = args.corp_code
        else:
            # --name 생략 시 DB에서 종목명/corp_code 자동 조회
            info = asyncio.run(get_stock_info(args.ticker))
            company_name = info["company_name"]
            corp_code = info["corp_code"] or args.corp_code
            logger.info("종목명 자동 조회: %s → %s", args.ticker, company_name)

        result = asyncio.run(
            run(
                ticker=args.ticker,
                company_name=company_name,
                corp_code=corp_code,
                output_path=args.output,
                save_db=args.save,
            )
        )

        if result is None:
            sys.exit(1)


if __name__ == "__main__":
    main()
