import json
import random

from app.checklist import SEED_PATH, validate
from app.scoring import score

CL = json.loads(SEED_PATH.read_text(encoding="utf-8"))
ALL_TOP = {"contact": "yes", "needs": "yes", "identification": "yes", "consultation": 20,
           "empathy": "yes", "dialog_management": 10, "closing": "yes", "gross_errors": "no"}


def res(**over):
    vals = {**ALL_TOP, **over}
    return {k: ({"verdict": "scale", "value": v} if isinstance(v, (int, float)) else {"verdict": v, "value": None})
            | {"status": "ok"} for k, v in vals.items()}


def test_seed_is_valid_and_matches_pdf():
    assert validate(CL) == []
    w = {c["id"]: c["weight"] for c in CL["criteria"]}
    assert w == {"contact": 10, "needs": 15, "identification": 20, "consultation": 20, "empathy": 15,
                 "dialog_management": 15, "closing": 5, "gross_errors": 0}


def test_all_top_is_100():
    assert score(CL, res())["total"] == 100.0


def test_no_gives_zero_for_that_weight():
    assert score(CL, res(contact="no"))["total"] == 90.0


def test_scale_is_proportional():
    assert score(CL, res(consultation=15))["total"] == 95.0      # 15/20 of 20
    assert score(CL, res(dialog_management=7))["total"] == 95.5  # 7/10 of 15


def test_na_redistributes_weight_proportionally():
    b = score(CL, res(identification="na", contact="no"))
    assert b["total"] == 87.5  # 70/80
    contact = next(r for r in b["criteria"] if r["criterion_id"] == "contact")
    assert contact["max_points"] == 12.5  # 10 -> 12.5 after redistribution


def test_rounding_is_half_up_two_digits():
    # 70.5 / 80 * 100 = 88.125 -> 88.13 (banker's rounding would give 88.12)
    b = score(CL, res(needs="na", closing="na", consultation=15, dialog_management=7))
    assert b["total"] == 88.13


def test_no_applicable_criteria_total_not_computed():
    only = {"criteria": [c for c in CL["criteria"] if c["na_allowed"]], "sections": []}
    b = score(only, {c["id"]: {"verdict": "na", "status": "ok"} for c in only["criteria"]})
    assert b["total"] is None


def test_gross_error_is_flag_only():
    b = score(CL, res(gross_errors="yes"))
    assert b["critical_failed"] and b["critical_ids"] == ["gross_errors"]
    assert b["total"] == 100.0


def test_needs_review_is_provisional_not_na():
    r = res()
    r["empathy"] = {"status": "needs_review"}
    b = score(CL, r)
    assert b["provisional"] and b["total"] == 100.0


def test_sections_add_up():
    b = score(CL, res(contact="no"))
    assert [s["id"] for s in b["sections"]] == ["start", "safety", "flow"]
    assert abs(sum(s["points"] for s in b["sections"]) - b["total"]) < 0.02


def test_properties_random():
    rnd = random.Random(7)
    for _ in range(300):
        over = {}
        for c in CL["criteria"]:
            opts = [s["value"] for s in c["scale"]["steps"]] if c["answer_type"] == "scale" else ["yes", "no"]
            if c["na_allowed"]:
                opts.append("na")
            over[c["id"]] = rnd.choice(opts)
        b = score(CL, res(**over))
        assert b["total"] is None or 0 <= b["total"] <= 100
        # N/A == removing the criterion
        na_ids = [k for k, v in over.items() if v == "na"]
        if na_ids:
            removed = {**CL, "criteria": [c for c in CL["criteria"] if c["id"] not in na_ids]}
            assert score(removed, res(**over))["total"] == b["total"]
        # scaling all weights leaves the total unchanged
        k3 = {**CL, "criteria": [{**c, "weight": c["weight"] * 3} for c in CL["criteria"]]}
        assert score(k3, res(**over))["total"] == b["total"]
