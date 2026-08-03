"""Release 路径的 HostingProvider 解析。"""

from __future__ import annotations

from typing import Any

from orch.promotion.config import get_promotion_config
from orch.remote.factory import get_hosting_provider


def get_release_provider(project: str) -> Any | None:
    entry = get_promotion_config(project)
    return get_hosting_provider(entry, role="pr")
