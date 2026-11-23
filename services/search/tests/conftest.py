"""Path fixture untuk uji search service.

Uji mengimpor `services.search.app.*` (bukan `app.*`), karena beberapa service
lain (mis. kbbi-linker yang direktorinya memakai tanda hubung) sudah memakai
nama top-level `app`; memakai nama yang sama akan saling menimpa saat seluruh
suite dijalankan dalam satu proses.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # akar repo

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
