"""samba_collected_product.detail_html에서 무신사(msscdn) 광고 배너 <img> 일괄 제거.

이슈 #225 backfill — 기존 수집 데이터 정정.
무신사 sanitize 로직(`MusinsaClient._sanitize_desc_html`)을 그대로 재사용한다.

사용법 (프로덕션 VM 컨테이너):
    # 1) DRY-RUN — 영향 row 수와 샘플 1건의 before/after 확인
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \
        -m backend.scripts.backfill_msscdn_banner --dry-run

    # 2) 실제 UPDATE 실행
    docker exec samba-samba-api-1 /app/backend/.venv/bin/python \
        -m backend.scripts.backfill_msscdn_banner --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from backend.db.orm import get_write_session
from backend.domain.samba.proxy.musinsa import MusinsaClient


async def _run(dry_run: bool) -> int:
    sanitize = MusinsaClient._sanitize_desc_html

    async with get_write_session() as session:
        # 후보 추출 — msscdn URL 포함 + 무신사 소싱 한정
        result = await session.execute(
            text(
                """
                SELECT id, detail_html
                FROM samba_collected_product
                WHERE source_site = 'MUSINSA'
                  AND detail_html LIKE '%msscdn.net%'
                """
            )
        )
        rows = result.fetchall()
        print(f"[backfill] 후보 row {len(rows)}건")

        changed: list[tuple[str, str, str]] = []
        for row in rows:
            pid, html = row[0], row[1] or ""
            new_html = sanitize(html)
            if new_html != html:
                changed.append((pid, html, new_html))

        print(f"[backfill] 실제 변경 필요 row {len(changed)}건")

        if changed:
            sample_id, before, after = changed[0]
            print(f"[backfill] 샘플 id={sample_id}")
            print(f"  before len={len(before)} / after len={len(after)}")
            # 너무 길면 잘라서 표시
            _b = before[:400] + ("..." if len(before) > 400 else "")
            _a = after[:400] + ("..." if len(after) > 400 else "")
            print(f"  before head: {_b}")
            print(f"  after  head: {_a}")

        if dry_run:
            print("[backfill] DRY-RUN 모드 — UPDATE 미실행")
            return 0

        # 실제 UPDATE
        for pid, _, new_html in changed:
            await session.execute(
                text(
                    "UPDATE samba_collected_product SET detail_html = :h WHERE id = :i"
                ),
                {"h": new_html, "i": pid},
            )
        await session.commit()
        print(f"[backfill] UPDATE 완료 — {len(changed)}건 반영")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="영향 row 수만 출력")
    group.add_argument("--apply", action="store_true", help="실제 UPDATE 실행")
    args = parser.parse_args()

    rc = asyncio.run(_run(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
