"""Mint JWT untuk pengujian lokal (dev).

Dipakai karena auth service (F1) belum diimplementasikan. Jalankan dengan
JWT_SECRET yang sama dengan container, contoh:

    docker compose exec poem python -c \
      "from shared.security import create_token; print(create_token(user_id=1, username='tester', nama='Tester'))"

Atau lokal (butuh PyJWT terpasang):

    $env:JWT_SECRET="ganti_secret_jwt_panjang_acak_minimal_32_karakter"
    python scripts/mint_token.py 1 tester
"""

from __future__ import annotations

import sys
from pathlib import Path

# Skrip ini bisa dijalankan langsung (`python scripts/mint_token.py`), dan
# sys.path[0] saat itu adalah folder scripts/, bukan akar repo — sehingga
# `shared` tidak terimport. Tambahkan akar repo ke sys.path secara eksplisit.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.security import create_token  # noqa: E402


def main() -> None:
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    username = sys.argv[2] if len(sys.argv) > 2 else "tester"
    print(create_token(user_id=user_id, username=username, nama=username.title()))


if __name__ == "__main__":
    main()
