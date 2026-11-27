"""Memangkas CSV KBBI sebelum diimpor di sandbox, agar import lebih cepat.

Yang disimpan: kata tunggal berhuruf saja (nlp-worker hanya mengambil token
[A-Za-z]+ dari puisi) dan baris definisi pertama tiap kata. Frasa, peribahasa,
kata ulang, serta makna kedua dan seterusnya dibuang.

Pemakaian: python3 kbbi_ringkas.py masuk.csv keluar.csv
"""

import csv
import re
import sys


def ringkas(masuk: str, keluar: str) -> int:
    sudah: set[str] = set()
    with open(masuk, encoding="utf-8-sig", newline="") as sumber, \
            open(keluar, "w", encoding="utf-8", newline="") as hasil:
        pembaca = csv.DictReader(sumber)
        penulis = csv.DictWriter(hasil, pembaca.fieldnames)
        penulis.writeheader()
        for baris in pembaca:
            kata = (baris["key"] or "").strip()
            if not re.fullmatch(r"[A-Za-z]+", kata) or not (baris["submakna"] or "").strip():
                continue
            if kata.lower() in sudah:
                continue
            sudah.add(kata.lower())
            penulis.writerow(baris)
    return len(sudah)


if __name__ == "__main__":
    print(f"CSV diringkas: {ringkas(sys.argv[1], sys.argv[2]):,} kata", flush=True)
