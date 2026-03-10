"""
DB 저장 / 조회: 분석 결과 저장, 미분석 종목 조회
psycopg2 기반 (asyncio.to_thread로 비동기 래핑)
"""
import asyncio
import logging

import psycopg2
import psycopg2.extras

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

logger = logging.getLogger("stock_analyst.db_writer")


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
# 분석 결과 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def save_result(ticker: str, synthesis: dict, outlook: dict) -> None:
    """
    Pass 2 최종 결과를 DB에 저장한다.
    - stock.stock_synthesis: advantage / risk / viewpoint
    - stock.analyze_disclosure: short_term / mid_term / long_term
    중복 시 항상 최신 데이터로 덮어쓴다.
    """
    def _write():
        conn = _get_conn()
        cur = conn.cursor()
        try:
            for insight_type in ("advantage", "risk", "viewpoint"):
                cur.execute(
                    """
                    INSERT INTO stock.stock_synthesis (isu_short_code, insight_type, summary)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (isu_short_code, insight_type) DO UPDATE
                        SET summary = EXCLUDED.summary
                    """,
                    (ticker, insight_type, synthesis.get(insight_type, "")),
                )

            horizon_map = {"short": "short_term", "mid": "mid_term", "long": "long_term"}
            for key, horizon in horizon_map.items():
                cur.execute(
                    """
                    INSERT INTO stock.analyze_disclosure (isu_short_code, horizon, summary)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (isu_short_code, horizon) DO UPDATE
                        SET summary = EXCLUDED.summary
                    """,
                    (ticker, horizon, outlook.get(key, "")),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    try:
        await asyncio.to_thread(_write)
        logger.info("DB 저장 완료: %s", ticker)
    except Exception as e:
        logger.error("DB 저장 실패 (%s): %s", ticker, e)
        raise


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 분석 여부 확인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def already_analyzed(ticker: str) -> bool:
    """해당 종목이 이미 analyze_disclosure에 저장되어 있는지 확인한다."""
    def _query():
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM stock.analyze_disclosure WHERE isu_short_code = %s",
            (ticker,),
        )
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count

    try:
        count = await asyncio.to_thread(_query)
        return count > 0
    except Exception as e:
        logger.error("분석 여부 확인 실패 (%s): %s", ticker, e)
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 미분석 종목 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_unanalyzed_stocks(limit: int = 10) -> list[str]:
    """
    stock_disclosure에 공시가 있고, analyze_disclosure에 분석이 없는 종목을 반환한다.
    """
    def _query():
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sd.isu_short_code
            FROM stock.stock_disclosure sd
            LEFT JOIN stock.analyze_disclosure ad
                ON sd.isu_short_code = ad.isu_short_code
            WHERE ad.isu_short_code IS NULL
            GROUP BY sd.isu_short_code
            LIMIT %s
            """,
            (limit,),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.error("미분석 종목 조회 실패: %s", e)
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 종목 정보 조회 (corp_code, 종목명)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_stock_info(ticker: str) -> dict:
    """
    stock.stock 테이블에서 종목명, corp_code를 조회한다.
    반환: {"company_name": ..., "corp_code": ...}
    """
    def _query():
        conn = _get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT corp_code, description
            FROM stock.stock
            WHERE isu_short_code = %s
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
            return {"company_name": ticker, "corp_code": ""}
        return {
            "company_name": row["description"] or ticker,
            "corp_code": row["corp_code"] or "",
        }
    except Exception as e:
        logger.error("종목 정보 조회 실패 (%s): %s", ticker, e)
        return {"company_name": ticker, "corp_code": ""}
