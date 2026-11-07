"""Pure scoring per the checklist PDF, p.2 «Порядок расчёта»:
yes = full weight, no = 0, scale proportional (top step = full weight),
N/A weight redistributed proportionally, total rounded to 2 digits, gross errors flagged separately."""
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction


def q(x, digits):
    # Decimal HALF_UP on exact fractions; never round(), it's banker's rounding
    if x is None:
        return None
    x = Fraction(x)
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return float(d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def F(v):
    return Fraction(str(v))


def normalized(c, verdict, value):
    if c["answer_type"] == "binary":
        return Fraction(1 if verdict == "yes" else 0)
    vals = [F(s["value"]) for s in c["scale"]["steps"]]
    lo, hi = min(vals), max(vals)
    return (F(value) - lo) / (hi - lo)


def score(checklist, results):
    """results: {criterion_id: {"verdict", "value", "status", "source"}} with final values.
    Missing / needs_review criteria are left out and the total is marked provisional."""
    sc = checklist.get("scoring", {})
    scale_max = F(sc.get("scale_max", 100))
    digits = int(sc.get("rounding_digits", 2))

    rows, applicable, critical_ids, provisional = [], [], [], False
    for c in checklist["criteria"]:
        r = results.get(c["id"])
        st = "missing" if r is None else r.get("status", "ok")
        row = {"criterion_id": c["id"], "title": c["title"], "section_id": c.get("section_id"),
               "weight": c["weight"], "is_critical": bool(c.get("is_critical")),
               "verdict": r.get("verdict") if r else None, "value": r.get("value") if r else None,
               "status": st, "source": r.get("source", "original") if r else None,
               "effective_weight": None, "points": None, "max_points": None}
        rows.append(row)
        if st != "ok":
            provisional = True
            continue
        if c.get("is_critical") and row["verdict"] == "yes":
            critical_ids.append(c["id"])
        if row["verdict"] != "na" and c["weight"] > 0:
            applicable.append((row, c))

    wsum = sum((F(c["weight"]) for _, c in applicable), Fraction(0))
    total, exact = None, {}
    if wsum > 0:
        total = Fraction(0)
        for row, c in applicable:
            mx = F(c["weight"]) / wsum * scale_max
            pts = normalized(c, row["verdict"], row["value"]) * mx
            total += pts
            exact[row["criterion_id"]] = (pts, mx)
            row.update(effective_weight=q(mx, digits), points=q(pts, digits), max_points=q(mx, digits))

    sections = []
    for s in checklist.get("sections", []):
        ids = [c["id"] for c in checklist["criteria"] if c.get("section_id") == s["id"] and c["weight"] > 0]
        if not ids:
            continue  # gross errors section has no points, it's a flag
        pts = sum((exact[i][0] for i in ids if i in exact), Fraction(0))
        mx = sum((exact[i][1] for i in ids if i in exact), Fraction(0))
        sections.append({"id": s["id"], "title": s["title"],
                         "points": q(pts, digits) if mx else None, "max_points": q(mx, digits) if mx else None,
                         "percent": q(pts / mx * 100, digits) if mx else None})

    return {"total": q(total, digits), "scale_max": float(scale_max), "provisional": provisional,
            "critical_failed": bool(critical_ids), "critical_ids": critical_ids,
            "criteria": rows, "sections": sections}
