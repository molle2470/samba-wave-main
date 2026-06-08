# -*- coding: utf-8 -*-
"""발주전자료 lookup 컨테이너 러너.

로컬 작업스케줄러(balju_fill.py)가 SSH+docker exec 로 호출한다.
stdin 으로 rows JSON 을 받아 balju_internal.lookup 을 실행하고
results JSON 을 stdout 으로 출력한다.

balju_internal.lookup / _decide 를 그대로 재사용 — 매칭·결정 로직 단일 출처.
토큰 불필요(컨테이너 내부 read DB 직접). HTTP 엔드포인트와 동일 코드.

호출 예:
  ssh <host> "sudo docker exec -i samba-samba-api-1 \
    /app/backend/.venv/bin/python3 /app/backend/scripts/balju_runner.py" < rows.json
"""

from __future__ import annotations

import asyncio
import json
import sys


async def _main() -> None:
    from backend.api.v1.routers.samba.balju_internal import (
        BaljuLookupReq,
        BaljuRowReq,
        lookup,
    )
    from backend.db.orm import get_read_sessionmaker

    payload = json.load(sys.stdin)
    rows = [BaljuRowReq(**r) for r in payload.get("rows", [])]

    sm = get_read_sessionmaker()
    async with sm() as session:
        out = await lookup(BaljuLookupReq(rows=rows), session=session)

    sys.stdout.write(json.dumps(out, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
