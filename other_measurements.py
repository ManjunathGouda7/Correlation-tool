"""
Tolerance comparison for the other measurements in the C2 EPR GRL reports
(same method as Rise / Fall Time, each measurement with its own limit).

Measurements (read from every C2_EPR GRL_USB_PD_Report_Run_*.csv):
  Bit Rate                  "Bit Rate is 298.993 Kbps. Test limit (270 ~ 330) Kbps"
  Bit Rate deviation        "Bit Rate is 0.004 %. Test limit: X < 0.25%"
  BIST pattern duration     "BIST pattern duration 47.03 mS [Limit : (30 ~ 60)ms]"
  Hard_Reset response       "Hard_Reset within 24~65ms.Obtained interval is 28.25ms"
  Timing check (Max)        "[PASS] Max = 6.1ms. Obtained time difference is 1.017ms"
  Data block timing         "Obtained time difference is 0.084ms, Expected time limit 0ms to 15ms"

Method (per measurement):
  - Tolerance = a percentage of the limit range of each value
        two-sided limit (lo ~ hi): range = hi - lo      one-sided (X < max): range = max
    so the ns tolerances of Rise/Fall are never applied to other units.
  - Two methods, default ±5 % and ±10 % of the range (TOLERANCE_PCT below).
  - One random number f in (-1, +1) is drawn once per method and measurement (fixed seed);
    every value of that measurement in every DUT / test case gets   value + f * pct * range.
  - Re-check against the limit; benchmark, worst case, chance of FAIL, graphs.

Run alone:  python other_measurements.py [--seed N] [--out folder]
(tolerance_compare.py also runs it into Comparison_Output\\Other_Measurements)
"""

import argparse
import html
import random
import re
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent
C2_DIR = ROOT / "C2_EPR"
NESTED_COPY_RE = re.compile(r"TID \d+[\\/].*TID \d+", re.I)
CHECK_ROW_RE = re.compile(r"^\d+,(.+?),(PASS|FAIL|INCOMPLETE|NA|WARNING|ABORTED)[,\s]*$")
TEST_ID_RE = re.compile(r"((?:TEST|COMMON)\.[A-Z0-9_.]+(?:#\d+)?)")
RUN_RE = re.compile(r"\d{5}_(.+?)_\d{4}_\d{2}_\d{2}")

# Edit here to change the two tolerance methods (percent of the limit range).
TOLERANCE_PCT = {"Tol_5pct": 5.0, "Tol_10pct": 10.0}


NO_LOW = float("-inf")  # measurement has only an upper limit (X < max / Max = ...)


def _two(lo, hi):
    return float(lo), float(hi), False


MEASUREMENTS = [
    # key, name, unit, regex, function(match) -> (value, lo, hi, strict_hi)
    ("bit_rate", "Bit Rate", "Kbps",
     r"Bit Rate is ([\d.]+) Kbps\. Test limit \(([\d.]+) ~ ([\d.]+)\) Kbps",
     lambda m: (float(m[1]), *_two(m[2], m[3]))),
    ("bit_rate_dev", "Bit Rate deviation", "%",
     r"Bit Rate is ([\d.]+) %\. Test limit: X < ([\d.]+)%",
     lambda m: (float(m[1]), NO_LOW, float(m[2]), True)),
    ("bist_duration", "BIST pattern duration", "ms",
     r"BIST pattern duration ([\d.]+) mS \[Limit : \(([\d.]+) ~ ([\d.]+)\)ms\]",
     lambda m: (float(m[1]), *_two(m[2], m[3]))),
    ("hard_reset", "Hard_Reset response", "ms",
     r"Hard_Reset within ([\d.]+)~([\d.]+)ms\.Obtained interval is ([\d.]+)ms",
     lambda m: (float(m[3]), *_two(m[1], m[2]))),
    ("timing_max", "Timing check (Max)", "ms",
     r"Max = ([\d.]+)ms\. Obtained time difference is ([\d.]+)ms",
     lambda m: (float(m[2]), NO_LOW, float(m[1]), False)),
    ("data_block", "Data block timing", "ms",
     r"Obtained time difference is ([\d.]+)ms, Expected time limit ([\d.]+)ms to ([\d.]+)ms",
     lambda m: (float(m[1]), *_two(m[2], m[3]))),
]
COMPILED = [(k, n, u, re.compile(p), f) for k, n, u, p, f in MEASUREMENTS]


# ---------------------------------------------------------------- extraction
def tid_of(path: Path) -> str:
    m = re.search(r"TID[ _](\d{5})", str(path))
    return m.group(1) if m else "?"


def run_of(path: Path) -> str:
    for part in reversed(path.parts):
        m = RUN_RE.match(part)
        if m:
            return m.group(1)
    return path.parent.name


def extract() -> pd.DataFrame:
    rows = []
    for f in sorted(C2_DIR.rglob("GRL_USB_PD_Report_Run_*.csv")):
        rel = f.relative_to(ROOT)
        if NESTED_COPY_RE.search(str(rel)):
            continue
        check, status = "", ""
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = CHECK_ROW_RE.match(line.strip())
            if m:
                check, status = m.group(1).strip(), m.group(2)
            for key, name, unit, rx, conv in COMPILED:
                for mm in rx.finditer(line):
                    value, lo, hi, strict = conv(mm)
                    t = TEST_ID_RE.search(check)
                    rows.append({"Measurement": name, "Key": key, "Unit": unit,
                                 "TID": tid_of(rel), "Run": run_of(rel),
                                 "Test_ID": t.group(1) if t else "", "Check": check,
                                 "Report_Status": status, "Value": value, "Lo": lo, "Hi": hi,
                                 "Strict_Hi": strict, "File": str(rel)})
    df = pd.DataFrame(rows)
    # one-sided limit: range = max; two-sided: hi - lo
    df["Range"] = df["Hi"] - df["Lo"].where(df["Lo"] > NO_LOW, 0.0)
    return df


# ---------------------------------------------------------------- evaluation
def in_limit(v, lo, hi, strict):
    """Element-wise; strict = upper limit is 'X < hi'."""
    upper = (v < hi).where(strict, v <= hi) if hasattr(strict, "where") else \
        ((v < hi) if strict else (v <= hi))
    return (v >= lo) & upper.astype(bool)


def evaluate(df: pd.DataFrame, f: float, pct: float) -> pd.DataFrame:
    off = f * pct / 100.0 * df["Range"]
    adj = (df["Value"] + off).clip(lower=0.0)   # times / percentages cannot go below 0
    res = in_limit(adj, df["Lo"], df["Hi"], df["Strict_Hi"])
    margin = pd.concat([adj - df["Lo"], df["Hi"] - adj], axis=1).min(axis=1)
    return pd.DataFrame({"Offset": off, "Adjusted": adj,
                         "Margin_%range": (100 * margin / df["Range"]).round(3),
                         "Result": res.map({True: "PASS", False: "FAIL"})})


def fail_chance(v, lo, hi, tol) -> float:
    """P(v + U(-tol, tol) outside [lo, hi])."""
    if lo <= 0:              # adjusted values are clamped at 0 -> low side never fails
        lo = NO_LOW
    if tol <= 0:
        return 0.0 if lo <= v <= hi else 1.0
    a, b = max(-tol, lo - v), min(tol, hi - v)
    return 1.0 - max(0.0, b - a) / (2 * tol)


# ---------------------------------------------------------------- output helpers
CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1b1b}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ccc}
table{border-collapse:collapse;font-size:12px;margin:8px 0}
th,td{border:1px solid #bbb;padding:3px 7px;text-align:left}th{background:#e8edf5}
td.pass{color:#11703a;font-weight:600}td.fail{color:#b3261e;font-weight:600}
.note{color:#555;font-size:13px}img{max-width:100%;border:1px solid #ddd;margin:6px 0}"""


def html_table(df: pd.DataFrame, limit=400) -> str:
    def cell(v):
        s = f"{v:.4g}" if isinstance(v, float) else html.escape(str(v))
        cls = {"PASS": "pass", "FAIL": "fail"}.get(str(v), "")
        return f'<td class="{cls}">{s}</td>' if cls else f"<td>{s}</td>"
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(cell(v) for v in r) + "</tr>"
                   for r in df.head(limit).itertuples(index=False))
    more = (f"<p class='note'>First {limit} of {len(df)} rows - see the CSV / Excel file.</p>"
            if len(df) > limit else "")
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def write_html(path: Path, title: str, body: str):
    path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}"
                    f"</title><style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
                    f"{body}</body></html>", encoding="utf-8")


def margin_plot(d: pd.DataFrame, name: str, path: Path):
    fig, ax = plt.subplots(figsize=(7, 3.6))
    bins = 30
    ax.hist(d["Margin_orig_%"], bins=bins, alpha=.6, label="Original", color="#95a5a6")
    for m, c in zip(TOLERANCE_PCT, ("#2f5fa7", "#c46a12")):
        ax.hist(d[f"Margin_{m}_%"], bins=bins, alpha=.5, label=m, color=c)
    ax.axvline(0, color="#b3261e", ls="--", label="Limit")
    ax.set_xlabel("Margin to the nearest limit (% of limit range)")
    ax.set_ylabel("Values")
    ax.set_title(name)
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------- main flow
def run(out: Path, seed: int, log=print) -> pd.DataFrame:
    if out.exists():
        shutil.rmtree(out)
    (out / "Graphs").mkdir(parents=True)
    df = extract()
    log(f"Other measurements: {len(df)} values from {df['File'].nunique()} reports")

    summaries, sheets = [], {}
    for key, name, unit, _, _ in MEASUREMENTS:
        d = df[df["Key"] == key].reset_index(drop=True)
        if d.empty:
            continue
        folder = out / key
        folder.mkdir()
        orig_ok = in_limit(d["Value"], d["Lo"], d["Hi"], d["Strict_Hi"])
        d["Result_orig"] = orig_ok.map({True: "PASS", False: "FAIL"})
        d["Margin_orig_%"] = (100 * pd.concat([d["Value"] - d["Lo"], d["Hi"] - d["Value"]],
                                              axis=1).min(axis=1) / d["Range"]).round(3)
        row = {"Measurement": name, "Unit": unit,
               "Limits": ", ".join(sorted({(f"<{hi:g}" if lo == NO_LOW else f"{lo:g}~{hi:g}")
                                           for lo, hi in zip(d['Lo'], d['Hi'])})[:6])
               + (" ..." if d[["Lo", "Hi"]].drop_duplicates().shape[0] > 6 else ""),
               "Values": len(d), "DUTs": d["TID"].nunique(),
               "Orig_PASS": int(orig_ok.sum()), "Orig_FAIL": int((~orig_ok).sum())}
        rng = random.Random(f"{seed}-{key}")
        for m, pct in TOLERANCE_PCT.items():
            f = rng.uniform(-1, 1)
            r = evaluate(d, f, pct)
            d[f"Offset_{m}"] = r["Offset"].round(6)
            d[f"Adjusted_{m}"] = r["Adjusted"].round(6)
            d[f"Margin_{m}_%"] = r["Margin_%range"]
            d[f"Result_{m}"] = r["Result"]
            tol = pct / 100.0 * d["Range"]
            worst = (in_limit((d["Value"] - tol).clip(lower=0.0), d["Lo"], d["Hi"], d["Strict_Hi"])
                     & in_limit(d["Value"] + tol, d["Lo"], d["Hi"], d["Strict_Hi"]))
            d[f"WorstCase_{m}"] = worst.map({True: "PASS", False: "FAIL"})
            d[f"P_fail_{m}_%"] = [round(100 * fail_chance(v, lo, hi, t), 1)
                                  for v, lo, hi, t in zip(d["Value"], d["Lo"], d["Hi"], tol)]
            row.update({f"{m}_f": round(f, 4), f"{m}_PASS": int((r["Result"] == "PASS").sum()),
                        f"{m}_FAIL": int((r["Result"] == "FAIL").sum()),
                        f"{m}_WorstCase_FAIL": int((~worst).sum()),
                        f"{m}_Expected_FAIL": round(d[f"P_fail_{m}_%"].sum() / 100, 2)})
        a, b = (f"Result_{m}" for m in TOLERANCE_PCT)
        d["Change"] = [x if x == y else f"{x} -> {y}" for x, y in zip(d[a], d[b])]
        row["Changed"] = int((d[a] != d[b]).sum())
        row["Lowest_margin_orig_%"] = d["Margin_orig_%"].min()
        summaries.append(row)
        sheets[key] = d

        d.to_csv(folder / "values.csv", index=False)
        per_tid = d.groupby("TID").agg(
            Values=("Value", "size"), Lowest_margin_orig_pct=("Margin_orig_%", "min"),
            **{f"FAIL_{m}": (f"Result_{m}", lambda s: int((s == "FAIL").sum())) for m in TOLERANCE_PCT},
            **{f"WorstFAIL_{m}": (f"WorstCase_{m}", lambda s: int((s == "FAIL").sum()))
               for m in TOLERANCE_PCT}).reset_index()
        per_tid.to_csv(folder / "per_tid.csv", index=False)
        margin_plot(d, name, folder / "margin.png")
        tight = d.nsmallest(25, "Margin_orig_%")[
            ["TID", "Run", "Test_ID", "Value", "Lo", "Hi", "Margin_orig_%"]
            + [c for m in TOLERANCE_PCT for c in (f"Result_{m}", f"WorstCase_{m}", f"P_fail_{m}_%")]]
        write_html(folder / "report.html", f"{name} ({unit}) - tolerance comparison",
                   html_table(pd.DataFrame([row]).T.reset_index().rename(
                       columns={"index": "Item", 0: "Value"}))
                   + "<img src='margin.png'><h2>Per TID</h2>" + html_table(per_tid)
                   + "<h2>Closest to the limit (25)</h2>" + html_table(tight)
                   + "<h2>All values</h2>" + html_table(d.drop(columns=["Key", "File"])))

    summary = pd.DataFrame(summaries)
    summary.to_csv(out / "Summary.csv", index=False)
    with pd.ExcelWriter(out / "Summary.xlsx", engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame([{"Method": m, "Percent_of_limit_range": p, "Seed": seed}
                      for m, p in TOLERANCE_PCT.items()]).to_excel(xw, sheet_name="Methods", index=False)
        for key, d in sheets.items():
            d.drop(columns=["Key"]).to_excel(xw, sheet_name=key[:31], index=False)

    # overview graphs
    ms = list(TOLERANCE_PCT)
    x = range(len(summary))
    for kind, title, fname in (("WorstCase_FAIL", "FAIL count at the worst case", "worst_case_fail"),
                               ("FAIL", "FAIL count with the random offset", "random_fail"),
                               ("Expected_FAIL", "Expected FAIL count (random offset)", "expected_fail")):
        fig, ax = plt.subplots(figsize=(9, 3.8))
        for k, (m, c) in enumerate(zip(ms, ("#2f5fa7", "#c46a12"))):
            bars = ax.bar([i + (k - .5) * .4 for i in x], summary[f"{m}_{kind}"], .4, label=m, color=c)
            ax.bar_label(bars, fontsize=8)
        ax.set_xticks(list(x), summary["Measurement"], rotation=15, fontsize=8)
        ax.set_title(title); ax.legend()
        fig.tight_layout(); fig.savefig(out / "Graphs" / f"{fname}.png", dpi=110); plt.close(fig)

    write_html(out / "Summary.html", "Other measurements - tolerance comparison",
               "<p class='note'>Tolerance = "
               + " / ".join(f"±{p:g}% ({m})" for m, p in TOLERANCE_PCT.items())
               + " of each value's limit range. One random number per method and measurement "
               f"(seed {seed}) is applied to every value of that measurement.</p>"
               + html_table(summary)
               + "".join(f"<img src='Graphs/{g}.png'>" for g in
                         ("random_fail", "worst_case_fail", "expected_fail"))
               + "<h2>Per measurement</h2><ul>"
               + "".join(f"<li><a href='{k}/report.html'>{n}</a></li>"
                         for k, n, *_ in MEASUREMENTS if k in sheets) + "</ul>")
    log(summary[["Measurement", "Values", "Orig_FAIL"]
                + [f"{m}_{s}" for m in ms for s in ("FAIL", "WorstCase_FAIL")]].to_string(index=False))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--out", default=str(ROOT / "Comparison_Output" / "Other_Measurements"))
    args = ap.parse_args()
    run(Path(args.out), args.seed)


if __name__ == "__main__":
    main()
