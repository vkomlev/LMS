"""tsk-567: распознавание числового ID в строке поискового запроса.

Тот же приём, что `app/services/task_search_service.py::_parse_task_number`
(tsk-353) — здесь вынесен в общий хелпер, чтобы `/tasks/search` и
`/materials/search` не заводили собственные копии.
"""
from __future__ import annotations

import re
from typing import Optional

#: "110" или "id-110" / "ID-110" — видимый номер записи.
_ID_RE = re.compile(r"^id-(\d+)$", re.IGNORECASE)


def parse_id_query(raw: str) -> Optional[int]:
    """"110" / "id-110" / "ID-110" -> 110. Иначе None (текстовый режим)."""
    stripped = raw.strip()
    if stripped.isdigit():
        return int(stripped)
    match = _ID_RE.match(stripped)
    return int(match.group(1)) if match else None
