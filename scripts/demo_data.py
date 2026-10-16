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
    ('1', 'Письмо 1 (после демо)', 'Спасибо за демо Solar Staff', 0, 0.3, 0.08, 0.004, 0.01),
    ('3', 'Письмо 1 (после регистрации)', 'Добро пожаловать в Solar Staff', 0, 0.33, 0.11, 0.004, 0.09),
    ('17', '💙 ПИСЬМО 1 Как Rambler&Co сократил', 'Как Rambler&Co сократил', 0, 0.15, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 2 Как BXB by Boxberry настроил', 'Как BXB by Boxberry настроил', 3, 0.13999999999999999, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 3 Кейс', 'Кейс', 6, 0.13, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 4 Почему на ОСНО с фрилансерами', 'Почему на ОСНО с фрилансерами', 9, 0.12, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 5 Фрилансеры по всему миру', 'Фрилансеры по всему миру', 12, 0.10999999999999999, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 6 Как Joom ускорил выплаты', 'Как Joom ускорил выплаты', 15, 0.09999999999999999, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 7 Как крупный и средний бизнес', 'Как крупный и средний бизнес', 18, 0.09, 0.015, 0.006, 0.02),
    ('17', '💙 ПИСЬМО 8 Эти слова в договоре', 'Эти слова в договоре', 21, 0.07999999999999999, 0.015, 0.006, 0.02),
    ('7', '💙 1. Не дозвонились: оставляем полезное', 'Не дозвонились: оставляем полезное', 0, 0.3, 0.02, 0.005, 0.04),
    ('7', '💙 2. Возможно, выплаты исполнителям', 'Возможно, выплаты исполнителям', 2, 0.3, 0.02, 0.005, 0.04),
    ('7', '💙 3. Сколько согласований нужно', 'Сколько согласований нужно', 4, 0.3, 0.02, 0.005, 0.04),
    ('7', '💙 4. Возможность компенсировать 22%', 'Возможность компенсировать 22%', 6, 0.3, 0.02, 0.005, 0.04),
    ('7', 'ABM Banks 5', 'ABM Banks 5', 0, 0.5, 0, 0, 0),
    ('18', '💙ПИСЬМО 1 Почему на ОСНО с фрилансерами безопаснее', 'Почему на ОСНО с фрилансерами безопаснее', 1, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 2 Как зарабатывать с Solar Staff', 'Как зарабатывать с Solar Staff', 4, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 3 3 способа работать с Solar Staff', '3 способа работать с Solar Staff', 7, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 4 Как организовать выплаты сотням фрилансеров', 'Как организовать выплаты сотням фрилансеров', 10, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 5 Возможность компенсировать 22%', 'Возможность компенсировать 22%', 13, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 6 Как Joom в 2 раза ускорил выплаты', 'Как Joom в 2 раза ускорил выплаты', 16, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 7 Как «Фибоначчи Digital» построил', 'Как «Фибоначчи Digital» построил', 19, 0.13, 0.012, 0.005, 0.03),
    ('18', '💙ПИСЬМО 8 Эти слова в договоре', 'Эти слова в договоре', 22, 0.13, 0.012, 0.005, 0.03),
]
VOLUME = {"1": 4, "3": 5, "17": 6, "7": 1, "18": 3}


def main():
    today = dt.date.today()
    start = today - dt.timedelta(days=160)
    rows = []
    for seq, name, subj, lag, or_, ctr, uns, bad in LETTERS:
        for i in range(160):
            day = start + dt.timedelta(days=i + lag)
            if day >= today:
                break
            if seq == "7" and (today - day).days <= 5:  # триггер «замолчал»
                continue
            base = VOLUME[seq] * (0.55 if " en" in name else 1)
            sent = max(0, int(random.gauss(base, base * 0.35)))
            if sent == 0:
                continue
            o = or_ * (0.6 if seq == "18" and "ПИСЬМО 2" in name and (today - day).days < 20 else 1)
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
