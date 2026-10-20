#!/usr/bin/env python3
"""Собирает сайт в _site/: страница + данные + алерты.

Алерты считаются здесь (а не в браузере), чтобы их можно было вывести
в сводку GitHub Actions и увидеть в истории коммитов.

Запуск:  python scripts/build.py [--data путь/к/daily.json]
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from common import ALERTS_FILE, DATA_FILE, METRICS, ROOT, detect_lang, load_config  # noqa: E402

MSK = dt.timezone(dt.timedelta(hours=3))


def scenario_meta(cfg, seqs):
    meta = {}
    for s in seqs:
        c = cfg.get("scenarios", {}).get(s, {})
        meta[s] = {
            "name": c.get("name") or f"Сценарий #{s}",
            "product": c.get("product") or cfg.get("default_product") or "Другое",
            "lang": c.get("lang", "auto"),
            "configured": s in cfg.get("scenarios", {}),
        }
    return meta


def row_lang(row, meta):
    lang = meta[row["seq"]]["lang"]
    return lang if lang in ("ru", "en") else detect_lang(row["subject"], row["name"])


# «1. …», «(2) …», «💙 ПИСЬМО 5 …», «Письмо 1 (после демо)»
NUM_PREFIX = re.compile(r"^[^\w(]*\(?(?:письмо\s*)?(\d+)(?:[.)]|\s)", re.I)


def assign_letters(rows, cfg):
    """Определяет номер письма в цепочке (step) для каждой строки.

    1) Порядок из config.json: scenarios.<ID>.letters.<язык> — список фрагментов
       темы или названия письма по порядку. Так одно письмо склеивается, даже если
       его переименовали в Sendsay.
    2) Номер в начале названия: «1. …», «(2) …».
    3) Иначе — по дате первой отправки, после известных писем (step_guess = True).
    """
    groups = defaultdict(list)
    for r in rows:
        groups[(r["seq"], r["lang"])].append(r)
    for (seq, lang), grs in groups.items():
        spec = cfg.get("scenarios", {}).get(seq, {}).get("letters", {}).get(lang, [])
        spec = [[m] if isinstance(m, str) else m for m in spec]
        variants = {}
        for r in grs:
            k = (r["name"], r["subject"])
            if k not in variants:
                variants[k] = {"first": r["d"], "last": r["d"], "subject": r["subject"] or r["name"]}
            v = variants[k]
            if r["d"] < v["first"]:
                v["first"] = r["d"]
            if r["d"] >= v["last"]:
                v["last"], v["subject"] = r["d"], r["subject"] or r["name"]
        steps = {}
        for (name, subj), v in variants.items():
            hay = f"{name} {subj}".lower()
            step = next((i + 1 for i, ms in enumerate(spec) if any(m.lower() in hay for m in ms)), None)
            if step is None:
                m = NUM_PREFIX.match(name or "")
                step = int(m.group(1)) if m else None
            steps[(name, subj)] = step
        known = [st for st in steps.values() if st]
        nxt = max(known or [0]) + 1
        guessed = set()
        for k, v in sorted(variants.items(), key=lambda kv: kv[1]["first"]):
            if steps[k] is None:
                steps[k] = nxt
                guessed.add(k)
                nxt += 1
        for r in grs:
            k = (r["name"], r["subject"])
            r["step"] = steps[k]
            r["step_guess"] = k in guessed
            r["letter"] = f"{lang}:{steps[k]}"


def drop_unlisted(rows, cfg):
    """Для сценариев с only_chain: true убирает письма вне цепочки
    (без номера в названии и без совпадения с letters) — например, старые выпуски."""
    strict = {s for s, c in cfg.get("scenarios", {}).items() if c.get("only_chain")}
    return [r for r in rows if not (r["seq"] in strict and r["step_guess"])]


def letter_labels(rows):
    """Подпись письма: «Письмо 2 (en) «последняя тема»»."""
    last = {}
    one_lang = len({r["lang"] for r in rows}) <= 1
    for r in rows:
        key = (r["seq"], r["letter"])
        if key not in last or r["d"] >= last[key]["d"]:
            last[key] = r
    return {k: f"Письмо {r['step']}{'?' if r['step_guess'] else ''}{'' if one_lang else ' (' + r['lang'] + ')'} «{r['subject'] or r['name']}»"
            for k, r in last.items()}


def load_conversion():
    """data/conversion.json от scripts/fetch_conversion.py (если уже собран)."""
    path = ROOT / "data" / "conversion.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def window(rows, start, end):
    return [r for r in rows if start <= r["d"] <= end]


def total(rows):
    t = {m: 0 for m in METRICS}
    for r in rows:
        for m in METRICS:
            t[m] += r[m]
    return t


def pct(x):
    return f"{x * 100:.1f}%".replace(".", ",")


def compute_alerts(rows, meta, cfg, today):
    a = cfg["alerts"]
    labels = letter_labels(rows)
    D = lambda n: (today - dt.timedelta(days=n)).isoformat()  # noqa: E731
    last_full = D(1)
    alerts = []

    by_seq = defaultdict(list)
    for r in rows:
        by_seq[r["seq"]].append(r)

    for seq, srows in by_seq.items():
        name = meta[seq]["name"]
        # 1. Триггер замолчал: за последние N дней 0 отправок, а раньше письма шли.
        n = a["silence_days"]
        recent = total(window(srows, D(n), last_full))["sent"]
        before = total(window(srows, D(n + 28), D(n + 1)))["sent"]
        if recent == 0 and before >= a["silence_min_prev_sends"]:
            alerts.append({
                "level": "high", "seq": seq, "letter": None, "kind": "silence",
                "title": f"{name}: нет отправок {n} дн.",
                "detail": f"За предыдущие 4 недели ушло {before} писем, за последние {n} дн. — ни одного. "
                          f"Проверьте, что в сценарий приходит вход (событие/триггер).",
            })
            continue

        # 2. Резкое падение объёма: последняя неделя против средней за 4 недели до неё.
        week = total(window(srows, D(7), last_full))["sent"]
        base = total(window(srows, D(35), D(8)))["sent"] / 4
        if base >= a["volume_min_weekly"] and week < base * a["volume_drop_ratio"]:
            alerts.append({
                "level": "medium", "seq": seq, "letter": None, "kind": "volume",
                "title": f"{name}: объём упал до {week} писем/нед",
                "detail": f"Обычно ~{base:.0f} в неделю (среднее за 4 недели до этого).",
            })

        by_letter = defaultdict(list)
        for r in srows:
            by_letter[r["letter"]].append(r)

        for key, lrows in by_letter.items():
            letter = labels[(seq, key)]
            # 3–4. Ошибки доставки и отписки — одним алертом на письмо.
            # Окно длиннее недели и минимум событий: на малых объёмах одна отписка даёт ложный процент.
            nwin = a.get("rate_window_days", 14)
            w = total(window(lrows, D(nwin), last_full))
            min_cnt = a.get("min_events", 3)
            issues, details = [], []
            if (w["sent"] >= a["bounce_min_sent"] and w["bounced"] >= min_cnt
                    and w["bounced"] / w["sent"] > a["bounce_rate_max"]):
                issues.append(f"ошибки доставки {pct(w['bounced'] / w['sent'])}")
                details.append(f"ошибки: {w['bounced']} из {w['sent']} отправленных (порог {pct(a['bounce_rate_max'])}; "
                               f"проверьте исключение hard bounce и качество базы)")
            if (w["delivered"] >= a["unsub_min_delivered"] and w["unsubs"] >= min_cnt
                    and w["unsubs"] / w["delivered"] > a["unsub_rate_max"]):
                issues.append(f"отписки {pct(w['unsubs'] / w['delivered'])}")
                details.append(f"отписки: {w['unsubs']} из {w['delivered']} доставленных (порог {pct(a['unsub_rate_max'])})")
            if issues:
                alerts.append({
                    "level": "medium", "seq": seq, "letter": letter, "kind": "quality",
                    "title": f"{name} · {letter}: {', '.join(issues)}",
                    "detail": f"За {nwin} дней — " + "; ".join(details) + ".",
                })
            # 5. Просадка открываемости: 14 дней (без последних 2, открытия ещё копятся) против 8 недель до этого.
            cur = total(window(lrows, D(16), D(3)))
            prev = total(window(lrows, D(72), D(17)))
            if cur["delivered"] >= a["or_min_delivered"] and prev["delivered"] >= a["or_min_delivered"] and prev["u_opens"]:
                cur_or = cur["u_opens"] / cur["delivered"]
                prev_or = prev["u_opens"] / prev["delivered"]
                if cur_or < prev_or * a["or_drop_ratio"]:
                    alerts.append({
                        "level": "low", "seq": seq, "letter": letter, "kind": "open_rate",
                        "title": f"{name} · {letter}: OR упал до {pct(cur_or)}",
                        "detail": f"Было {pct(prev_or)} за 8 недель до этого. Проверьте тему, отправителя и попадание во «Входящие».",
                    })

    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda x: (order[x["level"]], x["title"]))
    return alerts


def write_summary(alerts, meta, rows, generated_at):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}
    lines = [f"## Триггерные рассылки — данные на {generated_at}", ""]
    lines.append(f"Сценариев: {len(meta)}, строк данных: {len(rows)}")
    lines.append("")
    if alerts:
        lines.append("### Алерты")
        lines += [f"- {icon[a['level']]} **{a['title']}** — {a['detail']}" for a in alerts]
    else:
        lines.append("Алертов нет ✅")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    data_path = DATA_FILE
    if "--data" in sys.argv:
        data_path = ROOT / sys.argv[sys.argv.index("--data") + 1]
    cfg = load_config()
    if data_path.exists():
        with open(data_path, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = {"generated_at": None, "source": None, "rows": []}
    excluded = set(map(str, cfg.get("exclude_scenarios", [])))
    rows = [r for r in payload["rows"] if r["seq"] not in excluded]

    # Порядок: сначала сценарии из config.json в их порядке, затем найденные автоматически.
    configured = list(cfg.get("scenarios", {}))
    configured = [s for s in configured if s not in excluded]
    discovered = sorted({r["seq"] for r in rows} - set(configured), key=int)
    meta = scenario_meta(cfg, configured + discovered)
    for r in rows:
        r["lang"] = row_lang(r, meta)
    assign_letters(rows, cfg)
    rows = drop_unlisted(rows, cfg)

    today = dt.datetime.now(MSK).date()
    alerts = compute_alerts(rows, meta, cfg, today) if rows else []

    # alerts.json коммитится вместе с данными — в истории git видно, когда что сработало.
    if data_path == DATA_FILE:
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": today.isoformat(), "alerts": alerts}, f, ensure_ascii=False, indent=1)
            f.write("\n")

    site = ROOT / "_site"
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ROOT / "site", site)
    report = {
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "today": today.isoformat(),
        "scenarios": meta,
        "alerts": alerts,
        "thresholds": {"unsub": cfg["alerts"]["unsub_rate_max"], "bounce": cfg["alerts"]["bounce_rate_max"]},
        "benchmarks": cfg.get("benchmarks"),
        "conversion": load_conversion(),
        "rows": rows,
    }
    with open(site / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
    (site / ".nojekyll").touch()

    write_summary(alerts, meta, rows, payload.get("generated_at"))
    print(f"Сайт собран: _site/ ({len(rows)} строк, {len(alerts)} алертов)")


if __name__ == "__main__":
    main()
