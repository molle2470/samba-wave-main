"""송장 라우팅 수정 dequeue SQL 검증 — 프로덕션 DB 직접.

수정 의도 검증:
  1) owner="" SSG tracking 잡을 ABCmart 담당 데몬(X-Poll-Site=ABCmart)이 dequeue 하나 (YES 기대)
  2) 비데몬(확장앱)은 SSG tracking 여전히 차단되나 (NO 기대)
  3) SSG detail 잡은 site 분담 유지 — ABCmart 폴링 데몬이 안 받나 (NO 기대)

임시 잡 INSERT → WHERE 절 재현 SELECT → 매칭 확인 → 삭제(원복).
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.db.orm import get_write_session

_UTC = timezone.utc
DAEMON_ONLY = ["SSG", "ABCMART", "GRANDSTAGE", "LOTTEON"]


def _build_where(device_id: str, allowed_sites, tracking_exception: bool) -> tuple[str, dict]:
    """get_next_job WHERE 절 재현 (FOR UPDATE/SKIP LOCKED 제외)."""
    conds = ["status='pending'", "expires_at > now()"]
    params: dict = {}
    # owner 필터
    if device_id:
        conds.append("(owner_device_id IS NULL OR owner_device_id='' OR owner_device_id=:dev)")
        params["dev"] = device_id
    else:
        conds.append("(owner_device_id IS NULL OR owner_device_id='')")
    # DAEMON_ONLY 가드 (비데몬 차단)
    if not device_id.startswith("samba-daemon-"):
        dph = ", ".join(f":do_{i}" for i in range(len(DAEMON_ONLY)))
        conds.append(f"(job_type='cancel_order' OR UPPER(site) NOT IN ({dph}))")
        for i, s in enumerate(DAEMON_ONLY):
            params[f"do_{i}"] = s
    # site 분담 필터
    if allowed_sites is not None:
        sl = [s.strip().upper() for s in allowed_sites if s.strip()]
        ph = ", ".join(f":s_{i}" for i in range(len(sl)))
        exc = "'cancel_order', 'tracking'" if tracking_exception else "'cancel_order'"
        conds.append(f"(job_type IN ({exc}) OR UPPER(site) IN ({ph}))")
        for i, s in enumerate(sl):
            params[f"s_{i}"] = s
    return " AND ".join(conds), params


async def main() -> None:
    async with get_write_session() as s:
        # 임시 잡 2개: SSG tracking owner="", SSG detail owner=""
        jid_trk = "TESTTRK_" + str(uuid.uuid4())[:8]
        jid_det = "TESTDET_" + str(uuid.uuid4())[:8]
        exp = datetime.now(_UTC) + timedelta(hours=1)
        for jid, jtype in [(jid_trk, "tracking"), (jid_det, "detail")]:
            await s.execute(
                text(
                    "INSERT INTO samba_sourcing_job "
                    "(request_id, site, job_type, status, owner_device_id, payload, created_at, expires_at) "
                    "VALUES (:r, 'SSG', :jt, 'pending', '', CAST(:p AS json), now(), :e)"
                ),
                {"r": jid, "jt": jtype, "p": '{"requestId":"' + jid + '"}', "e": exp},
            )
        await s.commit()

        async def count(label, device_id, allowed, trk_exc, target_jid):
            where, params = _build_where(device_id, allowed, trk_exc)
            params["tj"] = target_jid
            r = (
                await s.execute(
                    text(f"SELECT count(*) FROM samba_sourcing_job WHERE {where} AND request_id=:tj"),
                    params,
                )
            ).scalar()
            print(f"  {label}: {'매칭✅' if r else '미매칭❌'} (cnt={r})")
            return r

        print("=== 시나리오 (수정 후: tracking site 예외 ON) ===")
        print("[1] ABCmart담당 데몬(X-Poll=ABCmart)이 SSG tracking 받나 → 받아야(YES)")
        await count("    9fb4eb75/ABCmart/SSG-tracking", "samba-daemon-9fb4eb75dac64b7d", ["ABCmart"], True, jid_trk)
        print("[2] 비데몬(확장앱)이 SSG tracking 받나 → 차단돼야(NO)")
        await count("    ext-device/ABCmart/SSG-tracking", "ext-abc123", ["ABCmart"], True, jid_trk)
        print("[3] ABCmart담당 데몬이 SSG detail 받나 → site분담유지, 안받아야(NO)")
        await count("    9fb4eb75/ABCmart/SSG-detail", "samba-daemon-9fb4eb75dac64b7d", ["ABCmart"], True, jid_det)
        print("\n=== 대조 (수정 전: tracking 예외 OFF) ===")
        print("[4] (수정전이면) ABCmart담당 데몬이 SSG tracking → 미매칭이었음(NO)")
        await count("    9fb4eb75/ABCmart/SSG-tracking(예외OFF)", "samba-daemon-9fb4eb75dac64b7d", ["ABCmart"], False, jid_trk)

        # 원복
        await s.execute(text("DELETE FROM samba_sourcing_job WHERE request_id IN (:a,:b)"), {"a": jid_trk, "b": jid_det})
        await s.commit()
        print("\n임시 잡 삭제 완료(원복)")


if __name__ == "__main__":
    asyncio.run(main())
