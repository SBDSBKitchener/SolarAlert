"""Calibration study, part 3: the proposed final WARNING and ALERT shock rules, counted over 2024 and 2025."""
import json
src = open("tools/omni_shocks.py").read()
exec(src[:src.index('out = {"events"')])
RULES = {"WARNING": (45, 2, 1.8, 1.3, 1.25, 0.6), "ALERT": (120, 2, 1.5, 1.5, 2.0, 0.6)}

def level(c):
    def ok(dv, k, n, b, t, hold):
        if c["dV"] < dv or c["hold_dV"] is None or c["hold_dV"] < hold * c["dV"]:
            return False
        return sum([(c["N_ratio"] or 0) >= n, (c["B_ratio"] or 0) >= b, (c["T_ratio"] or 0) >= t]) >= k
    return "ALERT" if ok(*RULES["ALERT"]) else "WARNING" if ok(*RULES["WARNING"]) else None

text = []
for y in (2024, 2025):
    d = load(y)
    cands = candidates(d, 0, 366 * 1440, 20.0)
    got = [(level(c), c) for c in cands if level(c)]
    n_a = sum(1 for l, _ in got if l == "ALERT")
    text.append(f"\n== {y}: {n_a} ALERT, {len(got) - n_a} WARNING  (valid speed minutes {sum(x is not None for x in d['V'])}/{len(d['V'])})")
    for l, c in got:
        text.append(f"  {l:8s} {c['time']} dV={c['dV']} hold={c['hold_dV']} N x{c['N_ratio']} B x{c['B_ratio']} T x{c['T_ratio']} Bafter={c['B_after']}")
open("calib.txt", "w").write("\n".join(text) + "\n")
json.dump({"text": text}, open("calib.json", "w"))
print("\n".join(text)[:5000])
