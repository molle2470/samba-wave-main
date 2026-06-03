"""전송잡 최우선 마켓 dequeue SQL 검증 — 프로덕션 DB 직접 (읽기 전용).

수정 의도 검증:
  1) 바뀐 priority CASE/EXISTS 식이 프로덕션에서 syntax error 없이 도나
  2) market_type IN ('playauto','ssg','lottehome') 인 transmit 잡이 priority=0 으로 분류되나
  3) order_by(priority, size, created_at) 가 우선 마켓을 맨 앞에 정렬하나

INSERT 없음 — 기존 pending transmit 잡을 대상으로 분류만 확인.
"""

import asyncio
from sqlalchemy import text

from backend.db.orm import get_write_session

# repository._payload_account_array_sql("samba_jobs") 와 동일
_ACCOUNTS = (
    "ARRAY("
    "SELECT DISTINCT v FROM ("
    "SELECT json_array_elements_text(COALESCE(samba_jobs.payload->'target_account_ids', '[]'::json)) AS v "
    "UNION "
    "SELECT samba_jobs.payload->>'account_id' AS v "
    "WHERE COALESCE(samba_jobs.payload->>'account_id', '') <> '' "
    "UNION "
    "SELECT samba_jobs.payload->>'target_account_id' AS v "
    "WHERE COALESCE(samba_jobs.payload->>'target_account_id', '') <> ''"
    ") account_ids)"
)

# repository._priority_market_key 와 동일한 EXISTS 식
_PRIORITY_EXISTS = (
    "EXISTS (SELECT 1 FROM samba_market_account ma "
    f"WHERE ma.id::text = ANY({_ACCOUNTS}) "
    "AND ma.market_type IN ('playauto', 'ssg', 'lottehome'))"
)


async def main() -> None:
    async with get_write_session() as session:
        # 1) 식 자체가 도는지 + 분류 카운트
        sql = text(
            "SELECT "
            f"CASE WHEN job_type='transmit' AND {_PRIORITY_EXISTS} THEN 0 ELSE 1 END AS prio, "
            "COUNT(*) AS n "
            "FROM samba_jobs "
            "WHERE status='pending' AND job_type='transmit' "
            "GROUP BY 1 ORDER BY 1"
        )
        rows = (await session.execute(sql)).all()
        print("[1] pending transmit 잡 priority 분류:")
        if not rows:
            print("    (pending transmit 잡 없음 — 식은 에러 없이 실행됨)")
        for prio, n in rows:
            label = "최우선(playauto/ssg/lottehome)" if prio == 0 else "일반"
            print(f"    priority={prio} {label}: {n}건")

        # 2) 우선 마켓별 계정 수 (식이 실제 3마켓 매칭하는지 교차 확인)
        sql2 = text(
            "SELECT market_type, COUNT(*) FROM samba_market_account "
            "WHERE market_type IN ('playauto','ssg','lottehome') "
            "GROUP BY market_type ORDER BY market_type"
        )
        print("[2] 대상 마켓 계정 수:")
        for mt, n in (await session.execute(sql2)).all():
            print(f"    {mt}: {n}개")

        # 3) order_by 재현 — 상위 10개 prio 순서 확인
        sql3 = text(
            "SELECT id, "
            f"CASE WHEN job_type='transmit' AND {_PRIORITY_EXISTS} THEN 0 ELSE 1 END AS prio, "
            "created_at "
            "FROM samba_jobs WHERE status='pending' AND job_type='transmit' "
            "ORDER BY prio ASC, created_at ASC LIMIT 10"
        )
        print("[3] 정렬 상위 10개 (prio 오름차순 = 최우선 먼저):")
        for jid, prio, created in (await session.execute(sql3)).all():
            print(f"    prio={prio} {jid} {created}")


if __name__ == "__main__":
    asyncio.run(main())
