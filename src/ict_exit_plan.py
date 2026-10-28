"""Half at the nearer real swing/3R target; price breakeven for the rest."""
import math


def exit_plan(entry, stop, direction, swing_price, ratio=3.0):
    if direction not in (-1, 1) or not all(math.isfinite(x) and x > 0 for x in (entry, stop, swing_price)):
        return None
    risk = direction*(entry-stop)
    if risk <= 0 or direction*(swing_price-entry) <= 0:
        return None
    three_r = entry + direction*ratio*risk
    if three_r <= 0:
        return None
    near, far = sorted((swing_price, three_r), key=lambda price: direction*(price-entry))
    same = math.isclose(near, far, rel_tol=1e-10)
    return {"three_r": three_r, "swing_target": swing_price, "target1": near, "target2": far,
            "first_fraction": 1.0 if same else 0.5, "breakeven_stop": entry,
            "first_kind": "same" if same else ("swing" if near == swing_price else "3R")}
