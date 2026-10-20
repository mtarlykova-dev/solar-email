#!/usr/bin/env python3
"""Влияние цепочек на возврат лидов: Sendsay (кто получил письма) × amoCRM (кто вернулся).

Человек «вернулся», если в течение WINDOW дней после любого полученного письма цепочки
в воронке Inbound случилось одно из событий:
  reopen   — сделка контакта или его компании вышла из «Закрыто и не реализовано» в рабочий этап;
  new_lead — у контакта или его компании создана новая сделка в Inbound;
  inbound  — клиент сам вышел на связь: входящий звонок, входящее письмо, входящее сообщение в чате
             (на контакте, его компании или его сделке в Inbound).

События в первые IGNORE_HOURS (24 ч) после первого письма цепочки не считаются: письмо и сделка
часто создаются одним триггером (например, регистрация), это не возврат.

Сравнение: те, кто открывал или кликал письма цепочки, против тех, кто не открывал.

В репозиторий пишется только data/conversion.json со сводными цифрами и ID сделок amoCRM.
Адреса и имена не сохраняются и не выводятся в лог (лог публичного репозитория виден всем).

Переменные окружения: SENDSAY_LOGIN, SENDSAY_APIKEY, AMO_TOKEN.
Запуск:  python scripts/fetch_conversion.py
"""
import datetime as dt
import json
import os
import re
import statistics
import traceback
import sys
import time
from collections import Counter, defaultdict

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, load_config  # noqa: E402
from fetch_sendsay import SendsayError, call  # noqa: E402
from build import NUM_PREFIX  # noqa: E402

OUT = ROOT / "data" / "conversion.json"
STATUS_FILE = ROOT / "data" / "conversion_status.json"
STATUS = {"stages": [], "warnings": [], "error": None}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text):
    """Убирает адреса из текста ошибок: лог и статус лежат в публичном репозитории."""
    return EMAIL_RE.sub("<email>", str(text))[:500]


def stage(msg):
    msg = scrub(msg)
    print(msg, flush=True)
    STATUS["stages"].append(msg)


def warn(msg):
    msg = scrub(msg)
    print(f"::warning::{msg}", flush=True)
    STATUS["warnings"].append(msg)


def save_status(ok):
    STATUS["ok"] = ok
    STATUS["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    STATUS_FILE.parent.mkdir(exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(STATUS, f, ensure_ascii=False, indent=1)
        f.write("\n")
SUBDOMAIN = "solarstaff"
AMO = f"https://{SUBDOMAIN}.amocrm.ru/api/v4"
LOST = 143
DAY = 86400


# ---------------- Sendsay ----------------

def sendsay_all(login, key, select, flt, page=5000):
    out, skip = [], 0
    while True:
        data = call(login, key, {"action": "stat.uni", "select": select, "filter": flt,
                                 "skip": skip, "first": page})
        rows = data.get("list") or []
        out.extend(rows)
        if len(rows) < page:
            return out
        skip += page


def chunks(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def to_ts(v):
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        return int(float(v))
    s = str(v)[:19].replace("T", " ")
    fmt = "%Y-%m-%d %H:%M:%S" if len(s) > 10 else "%Y-%m-%d"
    return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone(dt.timedelta(hours=3))).timestamp())


def load_recipients(login, key, cfg):
    """{seq: {email: {"deliv": [(ts, step)], "engaged": bool}}} + основа вовлечённости."""
    seqs = [s for s in cfg["scenarios"] if s not in set(cfg.get("exclude_scenarios", []))]
    issues = sendsay_all(login, key, ["issue.id", "issue.sequence.id", "issue.name"], [
        {"a": "issue.dt:YD", "op": ">=", "v": cfg["start_date"]},
        {"a": "issue.sequence.id", "op": "in", "v": seqs},
    ])
    issue_info = {}
    for iid, seq, name in issues:
        m = NUM_PREFIX.match(name or "")
        strict = cfg["scenarios"].get(str(seq), {}).get("only_chain")
        if strict and not m:
            continue  # выпуски вне цепочки (например, старые «ABM Banks»)
        issue_info[str(iid)] = (str(seq), int(m.group(1)) if m else None)
    stage(f"Sendsay: выпусков в цепочках — {len(issue_info)} (всего выпусков сценариев — {len(issues)})")

    people = {s: defaultdict(lambda: {"deliv": [], "engaged": False}) for s in seqs}
    statuses = Counter()
    skipped = 0
    for part in chunks(issue_info, 200):
        rows = sendsay_all(login, key, ["deliv.member.email", "deliv.issue.id", "deliv.issue.dt", "deliv.status"],
                           [{"a": "deliv.issue.id", "op": "in", "v": part}])
        for row in rows:
            if isinstance(row, dict):
                row = [row.get(k) for k in ("deliv.member.email", "deliv.issue.id", "deliv.issue.dt", "deliv.status")]
            email, iid, when, status = (list(row) + [None] * 4)[:4]
            statuses[str(status)] += 1
            ts = to_ts(when)
            if not email or ts is None or str(iid) not in issue_info:
                skipped += 1
                continue
            seq, step = issue_info[str(iid)]
            people[seq][email.strip().lower()]["deliv"].append((ts, step))
    stage(f"Sendsay: доставок — {sum(statuses.values())}, пропущено без адреса/даты — {skipped}, статусы: {dict(statuses)}")

    basis = []
    for kind in ("read", "click"):
        try:
            for part in chunks(issue_info, 200):
                rows = sendsay_all(login, key, [f"{kind}.member.email", f"{kind}.issue.id"],
                                   [{"a": f"{kind}.issue.id", "op": "in", "v": part}])
                for row in rows:
                    if isinstance(row, dict):
                        row = [row.get(f"{kind}.member.email"), row.get(f"{kind}.issue.id")]
                    email, iid = (list(row) + [None, None])[:2]
                    if not email or str(iid) not in issue_info:
                        continue
                    seq, _ = issue_info[str(iid)]
                    p = people[seq].get(email.strip().lower())
                    if p:
                        p["engaged"] = True
            basis.append(kind)
        except Exception as e:  # noqa: BLE001
            warn(f"Sendsay: {kind}.* недоступно ({e}) — вовлечённость без этого источника")
    return people, basis


# ---------------- amoCRM ----------------

class Amo:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, path, params=None):
        for attempt in range(6):
            r = self.s.get(AMO + path, params=params, timeout=60)
            time.sleep(0.15)  # лимит amoCRM — 7 запросов в секунду
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 204:
                return {}
            if r.status_code >= 400:
                raise RuntimeError(f"amoCRM {path}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"amoCRM {path}: не удалось после повторов")

    def paginate(self, path, params, key):
        params = dict(params, page=1)
        while True:
            data = self.get(path, params)
            items = (data.get("_embedded") or {}).get(key) or []
            yield from items
            if not (data.get("_links") or {}).get("next"):
                return
            params["page"] += 1


def inbound_pipeline(amo):
    for p in amo.get("/leads/pipelines")["_embedded"]["pipelines"]:
        if p["name"].strip().lower().startswith("inbound"):
            return p["id"]
    raise RuntimeError("Не нашла воронку Inbound")


def match_contacts(amo, emails):
    """email -> {"contacts": set, "companies": set}. Совпадение email строгое."""
    found = {}
    for i, email in enumerate(sorted(emails)):
        hits = set()
        companies = set()
        for c in amo.paginate("/contacts", {"query": email, "limit": 50}, "contacts"):
            vals = [v.get("value", "").strip().lower()
                    for f in (c.get("custom_fields_values") or []) if f.get("field_code") == "EMAIL"
                    for v in f.get("values", [])]
            if email in vals:
                hits.add(c["id"])
                companies.update(x["id"] for x in (c.get("_embedded") or {}).get("companies", []))
        if hits:
            found[email] = {"contacts": hits, "companies": companies}
        if (i + 1) % 200 == 0:
            stage(f"amoCRM: проверено адресов {i + 1} из {len(emails)}")
    return found


def leads_of(amo, entity, ids):
    """id контакта/компании -> список id сделок."""
    res = defaultdict(set)
    for part in chunks(ids, 50):
        for e in amo.paginate(f"/{entity}", {"limit": 250, "with": "leads", "filter[id][]": part}, entity):
            res[e["id"]].update(l["id"] for l in (e.get("_embedded") or {}).get("leads", []))
    return res


def lead_details(amo, ids):
    out = {}
    for part in chunks(ids, 100):
        for l in amo.paginate("/leads", {"limit": 250, "filter[id][]": part}, "leads"):
            out[l["id"]] = l
    return out


def reopen_events(amo, pipe, since):
    """lead_id -> [ts] выходов из «Закрыто и не реализовано» в Inbound."""
    res = defaultdict(list)
    base = {"limit": 100, "filter[type]": "lead_status_changed", "filter[created_at][from]": since}
    narrow = dict(base, **{"filter[value_before][leads_statuses][0][pipeline_id]": pipe,
                           "filter[value_before][leads_statuses][0][status_id]": LOST})
    try:
        events = list(amo.paginate("/events", narrow, "events"))
    except RuntimeError as e:
        warn(f"amoCRM: фильтр по этапу «до» не принят ({e}), беру все смены этапов и фильтрую сама")
        events = list(amo.paginate("/events", base, "events"))
    for e in events:
        before = ((e.get("value_before") or [{}])[0].get("lead_status") or {})
        after = ((e.get("value_after") or [{}])[0].get("lead_status") or {})
        if before.get("id") == LOST and after.get("id") != LOST and after.get("pipeline_id") == pipe:
            res[e["entity_id"]].append(e["created_at"])
    return res


def inbound_touches(amo, since):
    """(entity_type, entity_id) -> [ts] входящих обращений."""
    res = defaultdict(list)
    for entity in ("leads", "contacts", "companies"):
        params = {"limit": 250, "filter[note_type][]": ["call_in", "amomail_message"],
                  "filter[updated_at][from]": since}
        for n in amo.paginate(f"/{entity}/notes", params, "notes"):
            if n["note_type"] == "amomail_message" and not (n.get("params") or {}).get("income"):
                continue
            res[(entity, n["entity_id"])].append(n["created_at"])
    ent = {"lead": "leads", "contact": "contacts", "company": "companies"}
    params = {"limit": 100, "filter[type]": "incoming_chat_message", "filter[created_at][from]": since}
    for e in amo.paginate("/events", params, "events"):
        if e.get("entity_type") in ent:
            res[(ent[e["entity_type"]], e["entity_id"])].append(e["created_at"])
    return res


# ---------------- Расчёт ----------------

def in_window(deliv, events, window, ignore_hours):
    """События, которые случились в течение window дней после какого-то письма,
    но не раньше ignore_hours после первого письма."""
    start = min(deliv)[0] + ignore_hours * 3600
    return [e for e in events
            if e[0] >= start and any(d <= e[0] <= d + window * DAY for d, _ in deliv)]


def first_return(deliv, events, window):
    """Первое событие в окне: (ts, тип, номер последнего письма до него, время этого письма, сделка)."""
    deliv = sorted(deliv)
    best = None
    for ts, kind, ref in events:
        before = [d for d in deliv if d[0] <= ts <= d[0] + window * DAY]
        if before and (best is None or ts < best[0]):
            best = (ts, kind, before[-1][1], before[-1][0], ref)
    return best


def empty_group():
    return {"n": 0, "returned": 0, "by_type": Counter()}


def main():
    login, key, token = (os.environ.get(k, "").strip() for k in ("SENDSAY_LOGIN", "SENDSAY_APIKEY", "AMO_TOKEN"))
    if not (login and key and token):
        sys.exit("Нужны SENDSAY_LOGIN, SENDSAY_APIKEY и AMO_TOKEN")
    cfg = load_config()
    conv = cfg.get("conversion", {})
    window = conv.get("window_days", 60)
    ignore_hours = conv.get("ignore_first_hours", 24)
    since = to_ts(cfg["start_date"])

    people, basis = load_recipients(login, key, cfg)
    emails = {e for s in people.values() for e in s}
    stage(f"Получателей (уникальных адресов): {len(emails)}")

    amo = Amo(token)
    pipe = inbound_pipeline(amo)
    match = match_contacts(amo, emails)
    stage(f"amoCRM: найдено контактов для {len(match)} адресов из {len(emails)}")

    contact_ids = {c for m in match.values() for c in m["contacts"]}
    company_ids = {c for m in match.values() for c in m["companies"]}
    c_leads = leads_of(amo, "contacts", contact_ids)
    co_leads = leads_of(amo, "companies", company_ids) if company_ids else {}
    all_leads = set().union(*c_leads.values(), *co_leads.values()) if (c_leads or co_leads) else set()
    leads = {i: l for i, l in lead_details(amo, all_leads).items() if l["pipeline_id"] == pipe}
    reopen = reopen_events(amo, pipe, since)
    touches = inbound_touches(amo, since)
    stage(f"amoCRM: сделок Inbound — {len(leads)}, выходов из отказа — {sum(map(len, reopen.values()))}, "
          f"входящих обращений — {sum(map(len, touches.values()))}")

    def events_for(m):
        lids = set()
        for c in m["contacts"]:
            lids |= c_leads.get(c, set())
        for c in m["companies"]:
            lids |= co_leads.get(c, set())
        lids &= set(leads)
        ev = []
        for lid in lids:
            ev.append((leads[lid]["created_at"], "new_lead", lid))
            ev += [(ts, "reopen", lid) for ts in reopen.get(lid, [])]
            ev += [(ts, "inbound", lid) for ts in touches.get(("leads", lid), [])]
        for c in m["contacts"]:
            ev += [(ts, "inbound", None) for ts in touches.get(("contacts", c), [])]
        for c in m["companies"]:
            ev += [(ts, "inbound", None) for ts in touches.get(("companies", c), [])]
        return ev

    result = {}
    for seq, ps in people.items():
        s = {"recipients": len(ps), "matched": 0, "returned": 0, "by_type": Counter(),
             "by_letter": Counter(), "by_month": defaultdict(lambda: {"n": 0, "returned": 0}),
             "engaged": empty_group(), "not_engaged": empty_group(), "days": [], "lead_ids": set()}
        for email, p in ps.items():
            m = match.get(email)
            if not m or not p["deliv"]:
                continue
            s["matched"] += 1
            grp = s["engaged" if p["engaged"] else "not_engaged"]
            grp["n"] += 1
            month = dt.datetime.fromtimestamp(min(p["deliv"])[0], dt.timezone(dt.timedelta(hours=3))).strftime("%Y-%m")
            s["by_month"][month]["n"] += 1
            ev = in_window(p["deliv"], events_for(m), window, ignore_hours)
            hit = first_return(p["deliv"], ev, window)
            if not hit:
                continue
            ts, kind, step, dts, ref = hit
            # все типы событий в окне, чтобы видеть, как именно вернулись
            kinds = {k for _, k, _ in ev}
            s["returned"] += 1
            grp["returned"] += 1
            s["by_month"][month]["returned"] += 1
            s["by_letter"][str(step) if step else "?"] += 1
            s["days"].append((ts - dts) / DAY)
            for k in kinds:
                s["by_type"][k] += 1
                grp["by_type"][k] += 1
            if ref:
                s["lead_ids"].add(ref)
        result[seq] = {
            "recipients": s["recipients"], "matched": s["matched"], "returned": s["returned"],
            "by_type": dict(s["by_type"]), "by_letter": dict(s["by_letter"]),
            "by_month": dict(sorted(s["by_month"].items())),
            "engaged": {**s["engaged"], "by_type": dict(s["engaged"]["by_type"])},
            "not_engaged": {**s["not_engaged"], "by_type": dict(s["not_engaged"]["by_type"])},
            "median_days": round(statistics.median(s["days"]), 1) if s["days"] else None,
            "lead_ids": sorted(s["lead_ids"]),
        }
        stage(f"Сценарий {seq}: получили {s['recipients']}, найдены в amoCRM {s['matched']}, вернулись {s['returned']}")

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "window_days": window, "ignore_first_hours": ignore_hours, "pipeline": "Inbound", "start_date": cfg["start_date"],
        "engagement_basis": basis, "scenarios": result,
    }
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    stage(f"Записано: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
        save_status(True)
    except Exception as e:  # noqa: BLE001
        frames = traceback.extract_tb(e.__traceback__)[-4:]
        STATUS["error"] = {"type": type(e).__name__, "message": scrub(e),
                           "where": [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in frames]}
        save_status(False)
        print(f"::error::{type(e).__name__}: {scrub(e)}", flush=True)
        sys.exit(1)
