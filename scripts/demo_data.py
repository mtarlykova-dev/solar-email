#!/usr/bin/env python3
"""Генерирует ВЫДУМАННЫЕ данные для локальной проверки страницы (data/demo.json, не коммитится).

Запуск:
  python scripts/demo_data.py
  python scripts/build.py --data data/demo.json
  python -m http.server -d _site 8000
"""
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, make_row  # noqa: E402

random.seed(7)
# (сценарий, письмо, тема, сдвиг в днях, OR, CTR, отписки, ошибки)
LETTERS = [
    ("11", "Welcome 1", "Добро пожаловать в Solar Staff", 0, .31, .09, .004, .02),
    ("11", "Welcome 2", "Как подключить исполнителей за 15 минут", 2, .24, .03, .006, .02),
    ("11", "Welcome 3", "Закрывающие документы без ручной работы", 5, .2, .025, .008, .02),
    ("11", "Welcome en 1", "Welcome to Solar Staff", 0, .27, .07, .004, .03),
    ("12", "(1) Регистрация", "Остался один шаг: подпишите договор", 0, .28, .05, .005, .02),
    ("12", "(2) Регистрация", "Чем поможет менеджер", 3, .18, .02, .009, .02),
    ("13", "Прогрев 1", "Выплаты самозанятым и за рубеж", 0, .15, .012, .012, .03),
    ("13", "Прогрев 2", "Кейс: 300 исполнителей в месяц", 4, .12, .009, .014, .03),
    ("14", "Предотток", "Давно не было выплат — всё в порядке?", 0, .33, .06, .003, .01),
]
VOLUME = {"11": 12, "12": 9, "13": 15, "14": 3}


def main():
    today = dt.date.today()
    start = today - dt.timedelta(days=160)
    rows = []
    for seq, name, subj, lag, or_, ctr, uns, bad in LETTERS:
        for i in range(160):
            day = start + dt.timedelta(days=i + lag)
            if day >= today:
                break
            if seq == "14" and (today - day).days <= 5:  # триггер «замолчал»
                continue
            base = VOLUME[seq] * (0.55 if " en" in name else 1)
            sent = max(0, int(random.gauss(base, base * 0.35)))
            if sent == 0:
                continue
            o = or_ * (0.6 if seq == "11" and "Welcome 2" in name and (today - day).days < 20 else 1)
            deliv = sum(random.random() > bad for _ in range(sent))
            uo = sum(random.random() < o for _ in range(deliv))
            uc = sum(random.random() < ctr for _ in range(deliv))
            un = sum(random.random() < uns for _ in range(deliv))
            rows.append(make_row(day.isoformat(), seq, name, subj, {
                "sent": sent, "delivered": deliv, "bounced": sent - deliv,
                "opens": int(uo * 1.4), "u_opens": uo, "clicks": int(uc * 1.3), "u_clicks": uc, "unsubs": un,
            }))
    out = ROOT / "data" / "demo.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "source": "demo", "rows": rows}, f, ensure_ascii=False)
    print(f"Демо-данные: {out} ({len(rows)} строк)")


if __name__ == "__main__":
    main()
