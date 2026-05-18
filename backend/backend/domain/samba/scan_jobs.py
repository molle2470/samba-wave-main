"""brand-scan 등 100s 초과 가능 동기 API 를 비동기 polling 으로 우회하기 위한
in-memory job registry.

원인: Cloudflare Free/Pro 의 origin response timeout=100s. SSG 다이나핏 카테고리
스캔(~170s) 같은 long-running 요청이 100s 에 끊겨 frontend 가 Failed to fetch.

설계: 라우터가 즉시 job_id 반환 → background task 가 실제 작업 수행 →
frontend 가 1~2s 간격 polling. job 결과는 메모리에 1시간 유지 후 cleanup.

multi-process / multi-instance 환경 미지원 — 단일 컨테이너(uvicorn 단일 워커)
운영 가정. 향후 redis backed store 로 확장 가능하나 현 SSG concurrency=1
특성상 단일 워커가 자연스러운 직렬화 지점이라 in-memory 로 충분.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# job 결과 보관 시간 — 1시간 후 cleanup. progress polling 종료 후 페이지 리로드해도
# 결과를 다시 받을 수 있도록 충분히 여유.
_JOB_TTL_SEC = 3600


class ScanJobStore:
    """brand-scan / category-scan 등 long-running 작업의 job 레지스트리.

    상태 머신: pending → running → done | error.
    """

    _jobs: dict[str, dict[str, Any]] = {}
    _lock = asyncio.Lock()

    @classmethod
    def create(cls, kind: str, meta: dict[str, Any] | None = None) -> str:
        """새 job 등록 후 job_id 반환."""
        job_id = uuid.uuid4().hex[:16]
        cls._jobs[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "status": "running",
            "meta": meta or {},
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        cls._cleanup_expired()
        return job_id

    @classmethod
    def complete(cls, job_id: str, result: Any) -> None:
        job = cls._jobs.get(job_id)
        if not job:
            return
        job["status"] = "done"
        job["result"] = result
        job["updated_at"] = time.time()

    @classmethod
    def fail(cls, job_id: str, error: str) -> None:
        job = cls._jobs.get(job_id)
        if not job:
            return
        job["status"] = "error"
        job["error"] = error
        job["updated_at"] = time.time()

    @classmethod
    def get(cls, job_id: str) -> dict[str, Any] | None:
        return cls._jobs.get(job_id)

    @classmethod
    def _cleanup_expired(cls) -> None:
        now = time.time()
        expired = [
            jid for jid, j in cls._jobs.items() if now - j["updated_at"] > _JOB_TTL_SEC
        ]
        for jid in expired:
            cls._jobs.pop(jid, None)
