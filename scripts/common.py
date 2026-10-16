"""Общие функции: конфиг, формат строк данных, слияние.

Строка данных = агрегат за один день по одному письму сценария.
Никаких адресов и персональных данных: только суммы по выпуску.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "daily.json"
ALERTS_FILE = ROOT / "data" / "alerts.json"
CONFIG_FILE = ROOT / "config.json"

METRICS = ["sent", "delivered", "bounced", "opens", "u_opens", "clicks", "u_clicks", "unsubs"]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def detect_lang(*texts):
    text = " ".join(t or "" for t in texts)
    if not text.strip():
        return "unknown"
    return "ru" if CYRILLIC.search(text) else "en"


def make_row(day, seq, name, subject, values):
    row = {"d": day, "seq": str(seq), "name": (name or "").strip(), "subject": (subject or "").strip()}
    for m in METRICS:
        v = values.get(m) or 0
        row[m] = int(float(v))
    return row


def merge_rows(rows):
    """Склеивает строки с одинаковыми (день, сценарий, письмо, тема)."""
    acc = {}
    for r in rows:
        key = (r["d"], r["seq"], r["name"], r["subject"])
        if key not in acc:
            acc[key] = dict(r)
        else:
            for m in METRICS:
                acc[key][m] += r[m]
    return sorted(acc.values(), key=lambda r: (r["d"], r["seq"], r["name"], r["subject"]))


def load_rows():
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f).get("rows", [])


def save_rows(rows, source, generated_at):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": generated_at, "source": source, "rows": merge_rows(rows)}
    tmp = DATA_FILE.with_suffix(".tmp")
    # Одна строка данных = одна строка файла: так в git diff видно, какие дни и письма поменялись.
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('{"generated_at":%s,"source":%s,"rows":[\n' % (dump(generated_at), dump(source)))
        f.write(",\n".join(dump(r) for r in payload["rows"]))
        f.write("\n]}\n")
    os.replace(tmp, DATA_FILE)
    return payload
