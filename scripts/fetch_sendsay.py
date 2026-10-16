#!/usr/bin/env python3
"""Забирает статистику писем триггерных сценариев из Sendsay API (stat.uni).

Запрашиваются только агрегаты уровня выпуска (issue.*): отправлено, доставлено,
ошибки, открытия, клики, отписки. Адреса подписчиков не запрашиваются.

Переменные окружения:
  SENDSAY_LOGIN   — логин аккаунта Sendsay (обязательно)
  SENDSAY_APIKEY  — API-ключ (обязательно)
  FETCH_DAYS      — сколько последних дней перезапрашивать (по умолчанию весь период с start_date)

Запуск:  python scripts/fetch_sendsay.py [--debug]
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from common import load_config, load_rows, make_row, save_rows  # noqa: E402

API_HOST = "https://api.sendsay.ru"
PAGE = 5000

# Порядок полей в select определяет порядок колонок в ответе.
FIELDS = [
    ("seq", "issue.sequence.id"),
    ("d", "issue.dt:YD"),
    ("name", "issue.name"),
    ("subject", "issue.subject"),
    ("sent", "sum(issue.members)"),
    ("delivered", "sum(issue.deliv_ok)"),
    ("bounced", "sum(issue.deliv_bad)"),
    ("opens", "sum(issue.readed)"),
    ("u_opens", "sum(issue.u_readed)"),
    ("clicks", "sum(issue.clicked)"),
    ("u_clicks", "sum(issue.u_clicked)"),
    ("unsubs", "sum(issue.unsubed)"),
]


class SendsayError(RuntimeError):
    pass


def call(login, apikey, request, debug=False):
    """POST к API с обработкой REDIRECT (Sendsay может перенаправить на другой сервер)."""
    path = f"/general/api/v100/json/{login}"
    body = dict(request, apikey=apikey)
    for attempt in range(6):
        req = urllib.request.Request(
            API_HOST + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise SendsayError(f"HTTP {e.code}: {e.read()[:500]!r}")
        if debug:
            preview = json.dumps(data, ensure_ascii=False)[:1500]
            print(f"[debug] {request.get('action')} -> {preview}", file=sys.stderr)
        if data.get("REDIRECT"):
            path = data["REDIRECT"]
            continue
        if data.get("errors"):
            raise SendsayError(json.dumps(data["errors"], ensure_ascii=False))
        return data
    raise SendsayError("Слишком много перенаправлений от API")


def parse_list(data):
    rows = data.get("list")
    if rows is None:
        raise SendsayError(f"В ответе нет поля list: {list(data.keys())}")
    out = []
    for r in rows:
        if isinstance(r, dict):  # на случай ответа в виде словарей
            r = [r.get(api) for _, api in FIELDS]
        out.append(dict(zip([k for k, _ in FIELDS], r)))
    return out


def main():
    debug = "--debug" in sys.argv
    login = os.environ.get("SENDSAY_LOGIN", "").strip()
    apikey = os.environ.get("SENDSAY_APIKEY", "").strip()
    if not login or not apikey:
        sys.exit("Нет SENDSAY_LOGIN / SENDSAY_APIKEY. Добавьте их в Settings → Secrets and variables → Actions.")

    cfg = load_config()
    today = dt.date.today()
    start = dt.date.fromisoformat(cfg["start_date"])
    fetch_days = os.environ.get("FETCH_DAYS")
    since = max(start, today - dt.timedelta(days=int(fetch_days))) if fetch_days else start

    known = set(cfg.get("scenarios", {}))
    excluded = set(map(str, cfg.get("exclude_scenarios", [])))

    flt = [
        {"a": "issue.dt:YD", "op": ">=", "v": since.isoformat()},
        {"a": "issue.sequence.id", "op": "!is_null"},
    ]
    if not cfg.get("auto_discover", True):
        flt.append({"a": "issue.sequence.id", "op": "in", "v": sorted(known)})

    fetched, skip = [], 0
    while True:
        req = {
            "action": "stat.uni",
            "select": [api for _, api in FIELDS],
            "filter": flt,
            "order": ["issue.dt:YD", "issue.sequence.id", "issue.name"],
            "skip": skip,
            "first": PAGE,
        }
        page = parse_list(call(login, apikey, req, debug))
        fetched.extend(page)
        if len(page) < PAGE:
            break
        skip += PAGE

    new_rows = []
    for r in fetched:
        seq = str(r["seq"])
        if seq in excluded or not r["d"]:
            continue
        day = str(r["d"])[:10]
        new_rows.append(make_row(day, seq, r["name"], r["subject"], r))

    # Перезаписываем окно [since, сегодня]; более ранние строки (например, импорт из CSV) сохраняем.
    keep = [r for r in load_rows() if r["d"] < since.isoformat()]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = save_rows(keep + new_rows, "sendsay-api", now)

    seqs = sorted({r["seq"] for r in new_rows})
    unknown = [s for s in seqs if s not in known]
    print(f"Получено строк: {len(new_rows)} с {since}; сценарии: {', '.join(seqs) or '—'}")
    if unknown:
        print(f"Сценарии без названия в config.json: {', '.join(unknown)}")
    print(f"Всего строк в data/daily.json: {len(payload['rows'])}")

    # Сводка в GitHub Actions: какие ID сценариев пришли из API и какие письма в них.
    # По ней удобно сверить ID с config.json.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## Сценарии из Sendsay API", "", "| ID | В config.json | Писем отправлено | Примеры писем |", "|---|---|---|---|"]
        for s in seqs:
            rs = [r for r in new_rows if r["seq"] == s]
            names = sorted({r["name"] or r["subject"] for r in rs})[:3]
            lines.append(f"| {s} | {cfg['scenarios'][s]['name'] if s in known else '—'} | {sum(r['sent'] for r in rs)} | {'; '.join(names)} |")
        missing = [s for s in known if s not in seqs]
        if missing:
            lines += ["", f"Нет данных по сценариям из config.json: {', '.join(missing)}"]
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")


if __name__ == "__main__":
    try:
        main()
    except SendsayError as e:
        sys.exit(f"Ошибка Sendsay API: {e}")
