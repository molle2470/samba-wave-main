"""백엔드 _pc_last_seen + _pc_allowed_sites 덤프 (읽기 전용)."""

import time

from backend.api.v1.routers.samba.collector_autotune import (
    _pc_allowed_sites,
    _pc_last_seen,
)

now = time.time()
print("=== 등록 PC 전체 ===")
print(f"{'device_id':<45} {'sites':<35} {'last_seen(초전)':<15}")
for dev in sorted(_pc_allowed_sites.keys()):
    sites = sorted(_pc_allowed_sites.get(dev, set()))
    last = _pc_last_seen.get(dev, 0)
    age = int(now - last) if last else -1
    sites_str = ",".join(sites) if sites else "(빈)"
    print(f"{dev:<45} {sites_str:<35} {age:<15}")

print("\n=== 데몬(samba-daemon-*) 활성(60초 이내) ===")
for dev in sorted(_pc_allowed_sites.keys()):
    if not dev.startswith("samba-daemon-"):
        continue
    last = _pc_last_seen.get(dev, 0)
    age = int(now - last) if last else -1
    alive = age >= 0 and age < 60
    print(
        f"  {dev:<45} alive={alive} age={age}s sites={sorted(_pc_allowed_sites.get(dev, set()))}"
    )
