import hashlib
import json

from app import config, db

SEED_PATH = config.ROOT / "checklists" / "seed.json"


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def criterion_hash(checklist, criterion):
    # everything that reaches the prompt: the criterion itself + the shared rules
    return sha({"c": criterion, "rules": checklist.get("general_rules", "")})


def validate(cl):
    errs = []
    if not isinstance(cl, dict) or not isinstance(cl.get("criteria"), list) or not cl["criteria"]:
        return ["Чек-лист должен содержать хотя бы один критерий."]
    sections = {s.get("id") for s in cl.get("sections", [])}
    seen = set()
    for i, c in enumerate(cl["criteria"], 1):
        name = c.get("title") or f"Критерий {i}"
        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            errs.append(f"{name}: нужен id.")
        elif cid in seen:
            errs.append(f"{name}: id «{cid}» повторяется.")
        seen.add(cid)
        if not c.get("title"):
            errs.append(f"Критерий {i}: нужно название.")
        if c.get("section_id") and c["section_id"] not in sections:
            errs.append(f"{name}: неизвестная секция «{c['section_id']}».")
        w = c.get("weight")
        if not isinstance(w, (int, float)) or w < 0 or (w == 0 and not c.get("is_critical")):
            errs.append(f"{name}: вес должен быть больше 0 (0 допустим только для грубых ошибок).")
        t = c.get("answer_type")
        if t == "binary":
            if not c.get("yes_rule") or not c.get("no_rule"):
                errs.append(f"{name}: опишите, когда ставится «Да» и когда «Нет».")
        elif t == "scale":
            steps = (c.get("scale") or {}).get("steps") or []
            vals = [s.get("value") for s in steps]
            if len(steps) < 2 or not all(isinstance(v, (int, float)) for v in vals):
                errs.append(f"{name}: у шкалы должно быть минимум две числовые ступени.")
            elif len(set(vals)) != len(vals):
                errs.append(f"{name}: ступени шкалы не должны повторяться.")
            elif not all(s.get("label") for s in steps):
                errs.append(f"{name}: опишите каждую ступень шкалы.")
        else:
            errs.append(f"{name}: тип ответа должен быть binary или scale.")
        if c.get("na_allowed") and not c.get("na_rule"):
            errs.append(f"{name}: N/A разрешён, но условие N/A не задано.")
    return errs


def save_version(cl, note=""):
    errs = validate(cl)
    if errs:
        raise ValueError("; ".join(errs))
    with db.connect() as con:
        last = db.row(con, "SELECT version FROM checklist_versions ORDER BY id DESC LIMIT 1")
        active = db.get_state(con, "active_checklist_version_id")
        parent = db.row(con, "SELECT version FROM checklist_versions WHERE id=?", (active,)) if active else None
        cur = con.execute(
            "INSERT INTO checklist_versions(version,parent_version,json,hash,note,created_at) VALUES(?,?,?,?,?,?)",
            ((last["version"] + 1) if last else 1, parent["version"] if parent else None,
             canon(cl), sha(cl), note, db.now()))
        vid = cur.lastrowid
        db.set_state(con, "active_checklist_version_id", vid)
    return vid


def get_version(vid):
    with db.connect() as con:
        r = db.row(con, "SELECT * FROM checklist_versions WHERE id=?", (vid,))
    if not r:
        return None
    r["checklist"] = json.loads(r.pop("json"))
    return r


def active_version_id():
    with db.connect() as con:
        return db.get_state(con, "active_checklist_version_id")


def ensure_seed():
    if active_version_id() is None:
        save_version(json.loads(SEED_PATH.read_text(encoding="utf-8")), note="Исходный чек-лист из PDF")


def changed_criteria(old, new):
    """ids of criteria whose prompt-relevant content differs; these are the only ones that re-run"""
    oh = {c["id"]: criterion_hash(old, c) for c in old["criteria"]}
    return [c["id"] for c in new["criteria"] if oh.get(c["id"]) != criterion_hash(new, c)]


def load_seed_as_new_version():
    """Re-read checklists/seed.json into a new active version. Keeps the DB and cache, so
    applying it re-runs only the criteria whose text changed."""
    db.init()
    new = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cur = active_version_id()
    active = get_version(cur) if cur else None
    if active and sha(active["checklist"]) == sha(new):
        return None, []
    vid = save_version(new, note="Обновлено из seed.json")
    return vid, changed_criteria(active["checklist"], new) if active else [c["id"] for c in new["criteria"]]


if __name__ == "__main__":
    # python -m app.checklist --load-seed
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if "--load-seed" in sys.argv:
        v, changed = load_seed_as_new_version()
        print("seed.json уже совпадает с активной версией." if v is None else
              f"Новая активная версия id={v}. Изменены критерии: {', '.join(changed)}. "
              "Откройте Чек-лист → «Применить к набору» — пересчитаются только они.")
