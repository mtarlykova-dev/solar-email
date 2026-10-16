#!/usr/bin/env python3
"""Импорт ручных выгрузок «Экспорт» из сценария Sendsay (stat_uni_*.zip / *.csv).

ID сценария берётся из имени папки: imports/5/stat_uni_….zip -> сценарий 5
(номер из адреса app.sendsay.ru/automation/workflows/5). Число в конце имени файла
выгрузки — это не ID сценария, его не используем.
Строки этого сценария за даты из выгрузки заменяются, остальное не трогается.

Запуск:  python scripts/import_csv.py imports/5/stat_uni_*.zip
"""
import csv
import datetime as dt
import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from common import load_rows, make_row, save_rows  # noqa: E402

COLS = {
    "Отправлено": "sent", "Доставлено": "delivered", "Ошибки": "bounced",
    "Открытия": "opens", "Уник. открытия": "u_opens", "Клики": "clicks",
    "Уник. клики": "u_clicks", "Отписки": "unsubs",
}


def seq_from_path(p):
    folder = os.path.basename(os.path.dirname(os.path.abspath(p)))
    return folder if folder.isdigit() else None


def iter_csv_files(paths):
    for p in paths:
        seq = seq_from_path(p)
        if p.lower().endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.lower().endswith(".csv"):
                        yield seq, os.path.basename(n), z.read(n)
        else:
            with open(p, "rb") as f:
                yield seq, os.path.basename(p), f.read()


def decode(raw):
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def parse(seq, name, raw):
    if not seq:
        print(f"Пропуск {name}: положите файл в папку с ID сценария, например imports/5/")
        return None, []
    text = decode(raw)
    dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
    rows = []
    for r in csv.DictReader(io.StringIO(text), dialect=dialect):
        day = (r.get("Дата") or "").strip()[:10]
        if not day:
            continue
        if re.match(r"\d{2}\.\d{2}\.\d{4}", day):
            day = dt.datetime.strptime(day, "%d.%m.%Y").date().isoformat()
        values = {k: (r.get(c) or "0").replace(" ", "").replace(",", ".") for c, k in COLS.items()}
        rows.append(make_row(day, seq, r.get("Название выпуска"), r.get("Тема"), values))
    return seq, rows


def main():
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__)
    existing = load_rows()
    for seq, name, raw in iter_csv_files(paths):
        seq, rows = parse(seq, name, raw)
        if not rows:
            continue
        lo, hi = min(r["d"] for r in rows), max(r["d"] for r in rows)
        existing = [r for r in existing if not (r["seq"] == seq and lo <= r["d"] <= hi)] + rows
        print(f"{name}: сценарий {seq}, {len(rows)} строк, {lo} — {hi}")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = save_rows(existing, "csv-import", now)
    print(f"Всего строк в data/daily.json: {len(payload['rows'])}")


if __name__ == "__main__":
    main()
