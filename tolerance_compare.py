"""
Tolerance-based comparison of Rise / Fall Time (±10 ns vs ±20 ns).

Inputs (read only - nothing is changed):
  C2_EPR\\...\\GRL_USB_PD_Report_Run_*.csv      Rise/Fall of every C2 PD / Flip / Updated run
  Output\\VDPWR_EPR\\...\\GRL_USB_PD_Report_*.html  final VD PWR reports (old Corelation output);
                                                used for DUTs that have no C2 Rise/Fall run
                                                and for the "VD PWR Reports" sheet

Method:
  - One random offset per method, drawn once with a fixed seed:
        offset_10 ~ U(-10, +10) ns,   offset_20 ~ U(-20, +20) ns
  - The same offset is added to all 6 values (Rise/Fall Avg/Min/Max) of every run.
  - A run PASSes when adjusted Rise Min >= limit and adjusted Fall Min >= limit
    (the report only has a Minimum Limit, 300 ns).
  - Also reported: worst case (fixed -10 / -20 ns) and the probability of FAIL for
    a random offset in each range.

Output:
  Comparison_Output\\
    config.json
    Baseline\\                     measurements, reference values, VD PWR reports
    Tolerance_10ns\\
      Overall_Summary.csv/.html    all DUTs of this method
      TID_16394\\
        Reports\\  TID_16394.csv / .html
        Plots\\    TID_16394.png
        Summary\\  summary.csv / .html
      TID_.....\\
    Tolerance_20ns\\  (same layout)
    Benchmark_Comparison\\        Comparison_Table.xlsx/.csv, Comparison_Summary.csv,
                                  Comparison_Report.html, Graphs\\

Usage:
    python tolerance_compare.py                 # seed 20261123
    python tolerance_compare.py --seed 7
    python tolerance_compare.py --out D:\\some\\folder
"""

import argparse
import html
import json
import random
import re
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import grl_report_writer as grw  # noqa: E402
import other_measurements as om  # noqa: E402

ROOT = Path(__file__).resolve().parent
C2_DIR = ROOT / "C2_EPR"
VDPWR_OUT_DIR = ROOT / "Output" / "VDPWR_EPR"
DEFAULT_OUT = ROOT / "Comparison_Output"

METHODS = {"Tolerance_10ns": 10.0, "Tolerance_20ns": 20.0}
PARAMS = ["Rise_Avg", "Rise_Min", "Rise_Max", "Fall_Avg", "Fall_Min", "Fall_Max"]
# copies of other TIDs' runs nested inside a TID folder are skipped
NESTED_COPY_RE = re.compile(r"TID \d+[\\/].*TID \d+", re.I)

BLOCK_RE = re.compile(
    r"Rise time:\s*Average value = ([\d.]+) nS\s*Minimum value = ([\d.]+) nS\s*"
    r"Maximum value = ([\d.]+) nS\s*Minimum Limit = ([\d.]+) ns\s*"
    r"Fall time:\s*Average value = ([\d.]+) nS\s*Minimum value = ([\d.]+) nS\s*"
    r"Maximum value = ([\d.]+) nS\s*Minimum Limit = ([\d.]+) ns", re.I)
RESULT_RE = re.compile(r"BMC_PHY_TX_EYE_5\W+(PASS|FAIL|INCOMPLETE)", re.I)
RUN_TYPE_RE = re.compile(r"\d{5}_(PD(?:_port0)?(?:_flip)?(?:_updated)?)(?=_\d{4})", re.I)
XML_SCORE_RE = r'<test tcID="{}">.*?<score value="([^"]*)"'


# ---------------------------------------------------------------- extraction
def plain_text(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw.replace("&nbsp", " "))
    return re.sub(r"\s+", " ", html.unescape(raw))


def parse_block(text: str):
    m = BLOCK_RE.search(text)
    if not m:
        return None
    v = [float(x) for x in m.groups()]
    vals = dict(zip(PARAMS, v[0:3] + v[4:7]))
    vals["Limit_ns"] = min(v[3], v[7])
    r = RESULT_RE.search(text)
    vals["Report_Result"] = r.group(1).upper() if r else ""
    return vals


def run_type(path: Path) -> str:
    m = RUN_TYPE_RE.search(str(path))
    if not m:
        return "PD"
    t = m.group(1).lower().replace("flip", "Flip").replace("updated", "Updated")
    return "PD" + t[2:]


def tid_of(path: Path) -> str:
    m = re.search(r"TID[ _](\d{5})", str(path))
    return m.group(1) if m else "?"


def extract_c2():
    rows = []
    for f in sorted(C2_DIR.rglob("GRL_USB_PD_Report_Run_*.csv")):
        rel = f.relative_to(ROOT)
        if NESTED_COPY_RE.search(str(rel)):
            continue
        vals = parse_block(f.read_text(encoding="utf-8", errors="ignore"))
        if vals:
            rows.append({"TID": tid_of(rel), "Test_Case": run_type(rel),
                         "Source": "C2 EPR", **vals, "File": str(rel)})
    return rows


def xml_scores(run: Path) -> dict:
    xml = run / "PDMerged" / "PD_Merged.xml"
    text = xml.read_text(encoding="utf-8", errors="ignore") if xml.is_file() else ""
    out = {}
    for i in (1, 2, 3):
        tc = f"TEST.PD.PHY.ALL.{i}"
        m = re.search(XML_SCORE_RE.format(re.escape(tc)), text, re.S)
        out[f"PHY.ALL.{i}"] = m.group(1).strip().upper() if m else "-"
    return out


def extract_vdpwr():
    """One row per VD PWR output run that has the PHY.ALL tests."""
    rows = []
    if not VDPWR_OUT_DIR.is_dir():
        return rows
    for report in sorted(VDPWR_OUT_DIR.rglob("GRL_USB_PD_Report_*.html")):
        run = report.parent
        scores = xml_scores(run)
        if all(s == "-" for s in scores.values()):
            continue
        vals = parse_block(plain_text(report.read_text(encoding="utf-8", errors="ignore")))
        rows.append({"TID": tid_of(report.relative_to(ROOT)),
                     "Run_Folder": str(run.relative_to(ROOT)), **scores,
                     **({k: vals[k] for k in PARAMS + ["Limit_ns"]} if vals else {}),
                     "Report": str(report.relative_to(ROOT))})
    return rows


# ---------------------------------------------------------------- evaluation
def evaluate(df: pd.DataFrame, offset: float) -> pd.DataFrame:
    out = df[["TID", "Test_Case", "Source", "Limit_ns"]].copy()
    for p in PARAMS:
        out[p] = df[p]
        out[f"{p}_adj"] = (df[p] + offset).round(6)
    low = out[["Rise_Min_adj", "Fall_Min_adj"]].min(axis=1)
    out["Margin_ns"] = (low - out["Limit_ns"]).round(3)
    out["Result"] = (low >= out["Limit_ns"]).map({True: "PASS", False: "FAIL"})
    return out


def fail_probability(min_value: float, limit: float, tol: float) -> float:
    """P(min_value + U(-tol, tol) < limit)."""
    need = limit - min_value          # offset below this fails
    return max(0.0, min(1.0, (need + tol) / (2 * tol)))


def change_status(a: str, b: str) -> str:
    if a == b:
        return f"{a.title()} in both"
    return f"{a.title()} (±10) -> {b.title()} (±20)"


def counts(res: pd.DataFrame, label: str, tol: float, offset: float) -> dict:
    total, passed = len(res), int((res["Result"] == "PASS").sum())
    return {"Scope": label, "Method": f"±{tol:g} ns", "Offset_ns": round(offset, 6),
            "Total": total, "PASS": passed, "FAIL": total - passed,
            "Pass_%": round(100 * passed / total, 2) if total else 0,
            "Lowest_Margin_ns": res["Margin_ns"].min()}


# ---------------------------------------------------------------- output helpers
def html_table(df: pd.DataFrame) -> str:
    def cell(v):
        s = f"{v:.3f}" if isinstance(v, float) else html.escape(str(v))
        cls = {"PASS": "pass", "FAIL": "fail"}.get(str(v), "")
        return f'<td class="{cls}">{s}</td>' if cls else f"<td>{s}</td>"
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(cell(v) for v in r) + "</tr>"
                   for r in df.itertuples(index=False))
    return f"<table><tr>{head}</tr>{body}</table>"


CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1b1b}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ccc}
table{border-collapse:collapse;font-size:12px;margin:8px 0}
th,td{border:1px solid #bbb;padding:3px 7px;text-align:left}th{background:#e8edf5}
td.pass{color:#11703a;font-weight:600}td.fail{color:#b3261e;font-weight:600}
.note{color:#555;font-size:13px}img{max-width:100%;border:1px solid #ddd;margin:6px 0}
a{color:#2f5fa7}"""


def write_html(path: Path, title: str, body: str):
    path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}"
                    f"</title><style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
                    f"{body}</body></html>", encoding="utf-8")


def plot_dut(res: pd.DataFrame, tid: str, offset: float, path: Path):
    labels = list(res["Test_Case"])
    x = range(len(labels))
    w = 0.2
    fig, ax = plt.subplots(figsize=(max(5, 1.8 * len(labels)), 4))
    ax.bar([i - 1.5 * w for i in x], res["Rise_Min"], w, label="Rise Min (orig)", color="#9db8e0")
    ax.bar([i - 0.5 * w for i in x], res["Rise_Min_adj"], w, label="Rise Min (adj)", color="#2f5fa7")
    ax.bar([i + 0.5 * w for i in x], res["Fall_Min"], w, label="Fall Min (orig)", color="#f2c38f")
    ax.bar([i + 1.5 * w for i in x], res["Fall_Min_adj"], w, label="Fall Min (adj)", color="#c46a12")
    ax.axhline(res["Limit_ns"].iloc[0], color="#b3261e", ls="--", label="Min limit")
    lo = min(res["Rise_Min_adj"].min(), res["Fall_Min_adj"].min(), res["Limit_ns"].iloc[0])
    ax.set_ylim(lo - 40, None)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("ns")
    ax.set_title(f"TID {tid} - offset {offset:+.3f} ns")
    ax.legend(fontsize=7, ncol=3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------- method run
def run_method(name: str, tol: float, offset: float, base: pd.DataFrame, out: Path):
    """Per TID: <method>\\TID_xxxxx\\{Reports,Plots,Summary}; plus an overall summary."""
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    res = evaluate(base, offset)
    detail_cols = ["Test_Case", "Source", "Rise_Min", "Rise_Min_adj", "Fall_Min",
                   "Fall_Min_adj", "Limit_ns", "Margin_ns", "Result"]
    links = []

    for tid, grp in res.groupby("TID"):
        tdir = folder / f"TID_{tid}"
        for sub in ("Reports", "Plots", "Summary"):
            (tdir / sub).mkdir(parents=True, exist_ok=True)
        stem = f"TID_{tid}"

        # copies of the real GRL reports with the adjusted values
        grl_links = []
        for idx, row in grp.iterrows():
            if base.loc[idx, "Source"] != "C2 EPR":
                continue
            src_csv = ROOT / base.loc[idx, "File"]
            run_dir = src_csv.parent
            dest = tdir / "GRL_Reports" / run_dir.relative_to(C2_DIR / f"TID {tid}")
            st = grw.write_run(run_dir, dest, {p: row[p] for p in PARAMS}, offset,
                               row["Limit_ns"], f"±{tol:g} ns")
            if st["blocks"] == 0:
                print(f"  WARNING TID {tid} {row['Test_Case']}: no Rise/Fall values rewritten")
            grl_test = "FAIL" if st["eye_fail"] or st["test_was"] == "FAIL" else "PASS"
            report = dest / src_csv.with_suffix(".html").name
            res.loc[idx, "GRL_EYE_5"] = "FAIL" if st["eye_fail"] else "PASS"
            res.loc[idx, "GRL_PHY.ALL.2"] = grl_test
            res.loc[idx, "GRL_Report"] = report.relative_to(folder).as_posix()
            grl_links.append(f"<li><a href='../{report.relative_to(tdir).as_posix()}'>"
                             f"{row['Test_Case']}</a> - BMC_PHY_TX_EYE_5 "
                             f"{res.loc[idx, 'GRL_EYE_5']}, {grw.TC} {grl_test}</li>")
        grp = res.loc[grp.index]
        grl_html = ("<h2>GRL reports (adjusted values)</h2><ul>" + "".join(grl_links) + "</ul>"
                    if grl_links else "")
        cols = detail_cols + [c for c in ("GRL_EYE_5", "GRL_PHY.ALL.2") if c in grp.columns]

        grp.to_csv(tdir / "Reports" / f"{stem}.csv", index=False)
        plot_dut(grp, tid, offset, tdir / "Plots" / f"{stem}.png")
        write_html(tdir / "Reports" / f"{stem}.html", f"TID {tid} - ±{tol:g} ns",
                   f"<p class='note'>Offset applied to all 6 values: {offset:+.6f} ns</p>"
                   + html_table(grp[cols]) + grl_html
                   + f"<h2>All values</h2>{html_table(grp.drop(columns=['TID']))}"
                   + f"<img src='../Plots/{stem}.png'>")

        summ = pd.DataFrame([counts(grp, f"TID {tid}", tol, offset)])
        summ.to_csv(tdir / "Summary" / "summary.csv", index=False)
        write_html(tdir / "Summary" / "summary.html", f"TID {tid} - Summary ±{tol:g} ns",
                   html_table(summ) + "<h2>Per test case</h2>"
                   + html_table(grp[["Test_Case", "Rise_Min_adj", "Fall_Min_adj", "Margin_ns",
                                     "Result"] + [c for c in ("GRL_PHY.ALL.2",) if c in grp.columns]])
                   + grl_html
                   + f"<p><a href='../Reports/{stem}.html'>Report</a></p>"
                   + f"<img src='../Plots/{stem}.png'>")
        links.append(f"<li><a href='{stem}/Summary/summary.html'>TID {tid}</a> - "
                     f"{summ.loc[0, 'PASS']}/{summ.loc[0, 'Total']} PASS</li>")

    overall = pd.DataFrame([counts(res, "All DUTs", tol, offset)])
    per_tid = pd.DataFrame([counts(g, f"TID {t}", tol, offset) for t, g in res.groupby("TID")])
    pd.concat([overall, per_tid]).to_csv(folder / "Overall_Summary.csv", index=False)
    write_html(folder / "Overall_Summary.html", f"Overall Summary ±{tol:g} ns",
               html_table(overall) + "<h2>Per TID</h2>" + html_table(per_tid)
               + "<h2>TID folders</h2><ul>" + "".join(links) + "</ul>")
    summary = overall.drop(columns="Scope")
    return res, summary


# ---------------------------------------------------------------- benchmark graphs
def bench_graphs(summary: pd.DataFrame, table: pd.DataFrame, offsets: dict, graphs: Path):
    graphs.mkdir(parents=True, exist_ok=True)
    m = summary["Method"]

    fig, ax = plt.subplots(figsize=(5, 3.6))
    ax.bar([i - 0.2 for i in range(len(m))], summary["PASS"], 0.4, label="PASS", color="#2e8b57")
    ax.bar([i + 0.2 for i in range(len(m))], summary["FAIL"], 0.4, label="FAIL", color="#c0392b")
    ax.set_xticks(range(len(m)), m)
    ax.set_title("PASS vs FAIL count")
    ax.legend()
    fig.tight_layout(); fig.savefig(graphs / "pass_fail_count.png", dpi=110); plt.close(fig)

    fig, ax = plt.subplots(figsize=(5, 3.6))
    bars = ax.bar(m, summary["Pass_%"], color=["#2f5fa7", "#c46a12"])
    ax.bar_label(bars, fmt="%.1f%%")
    ax.set_ylim(0, 110); ax.set_title("Pass percentage")
    fig.tight_layout(); fig.savefig(graphs / "pass_percentage.png", dpi=110); plt.close(fig)

    changed = int((table["Change"].str.contains("->")).sum())
    fig, ax = plt.subplots(figsize=(5, 3.6))
    bars = ax.bar(["Unchanged", "Changed"], [len(table) - changed, changed],
                  color=["#7f8c8d", "#8e44ad"])
    ax.bar_label(bars)
    ax.set_title("Runs whose result changed (±10 vs ±20)")
    fig.tight_layout(); fig.savefig(graphs / "changed_results.png", dpi=110); plt.close(fig)

    # DUT-wise lowest margin: original, ±10, ±20
    dut = table.groupby("TID")[["Margin_orig_ns", "Margin_10_ns", "Margin_20_ns"]].min()
    fig, ax = plt.subplots(figsize=(10, 4))
    x = range(len(dut))
    for k, (col, lab, c) in enumerate([("Margin_orig_ns", "Original", "#95a5a6"),
                                       ("Margin_10_ns", f"±10 ({offsets['Tolerance_10ns']:+.2f})", "#2f5fa7"),
                                       ("Margin_20_ns", f"±20 ({offsets['Tolerance_20ns']:+.2f})", "#c46a12")]):
        ax.bar([i + (k - 1) * 0.27 for i in x], dut[col], 0.27, label=lab, color=c)
    ax.axhline(0, color="#b3261e", ls="--")
    ax.set_xticks(list(x), dut.index, rotation=30)
    ax.set_ylabel("Lowest margin to 300 ns limit (ns)")
    ax.set_title("DUT-wise margin to the limit")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(graphs / "dut_margin.png", dpi=110); plt.close(fig)

    # test-case-wise result map
    fig, ax = plt.subplots(figsize=(6, 0.32 * len(table) + 1.2))
    lab = (table["TID"] + " " + table["Test_Case"]).tolist()
    for j, col in enumerate(["Result_10", "Result_20"]):
        for i, r in enumerate(table[col]):
            ax.add_patch(plt.Rectangle((j, i), 0.95, 0.9,
                                       color="#2e8b57" if r == "PASS" else "#c0392b"))
            ax.text(j + 0.47, i + 0.45, r, ha="center", va="center", color="white", fontsize=7)
    ax.set_xlim(0, 2); ax.set_ylim(len(table), 0)
    ax.set_xticks([0.47, 1.47], ["±10 ns", "±20 ns"])
    ax.set_yticks([i + 0.45 for i in range(len(lab))], lab, fontsize=7)
    ax.set_title("Test-case-wise result")
    fig.tight_layout(); fig.savefig(graphs / "testcase_result.png", dpi=110); plt.close(fig)

    # probability of FAIL for a random offset, per run
    fig, ax = plt.subplots(figsize=(10, 4))
    x = range(len(table))
    ax.bar([i - 0.2 for i in x], table["P_fail_10_%"], 0.4, label="±10 ns", color="#2f5fa7")
    ax.bar([i + 0.2 for i in x], table["P_fail_20_%"], 0.4, label="±20 ns", color="#c46a12")
    ax.set_xticks(list(x), lab, rotation=60, fontsize=7, ha="right")
    ax.set_ylabel("% chance of FAIL")
    ax.set_title("Chance of FAIL for a random offset in each range")
    ax.legend()
    fig.tight_layout(); fig.savefig(graphs / "fail_probability.png", dpi=110); plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="±10 ns vs ±20 ns Rise/Fall comparison")
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--offset10", type=float, help="use this offset instead of the random one")
    ap.add_argument("--offset20", type=float, help="use this offset instead of the random one")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "Baseline").mkdir(parents=True)

    # Step 1 - extract
    c2 = extract_c2()
    vd = extract_vdpwr()
    c2_tids = {r["TID"] for r in c2}
    extra = [{"TID": r["TID"], "Test_Case": "PD", "Source": "VD PWR output",
              **{k: r[k] for k in PARAMS + ["Limit_ns"]}, "Report_Result": r["PHY.ALL.2"],
              "File": r["Report"]}
             for r in vd if r["TID"] not in c2_tids and "Rise_Min" in r]
    base = pd.DataFrame(c2 + extra).sort_values(["TID", "Test_Case"]).reset_index(drop=True)
    base.to_csv(out / "Baseline" / "measurements.csv", index=False)
    vd_df = pd.DataFrame(vd)
    vd_df.to_csv(out / "Baseline" / "vdpwr_reports.csv", index=False)

    # Step 2 - reference values
    ref = base[PARAMS].mean().round(6).rename("Reference_Avg_ns").to_frame()
    ref["Min_ns"], ref["Max_ns"] = base[PARAMS].min(), base[PARAMS].max()
    ref.index.name = "Parameter"
    ref.to_csv(out / "Baseline" / "reference_values.csv")

    # Step 3 - offsets (drawn once, reused everywhere)
    rng = random.Random(args.seed)
    offsets = {name: round(rng.uniform(-tol, tol), 6) for name, tol in METHODS.items()}
    for name, fixed in (("Tolerance_10ns", args.offset10), ("Tolerance_20ns", args.offset20)):
        if fixed is not None:
            if abs(fixed) > METHODS[name]:
                ap.error(f"{name}: offset {fixed} is outside ±{METHODS[name]:g} ns")
            offsets[name] = fixed
    config = {"created": datetime.now().isoformat(timespec="seconds"), "seed": args.seed,
              "fixed_offsets": {"Tolerance_10ns": args.offset10, "Tolerance_20ns": args.offset20},
              "offsets_ns": offsets, "tolerances_ns": METHODS,
              "rule": "PASS if Rise_Min+offset >= limit and Fall_Min+offset >= limit",
              "limit_ns": float(base["Limit_ns"].min()), "runs": len(base),
              "inputs": base["File"].tolist()}
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    # Steps 4-7 - reports per method, one folder per TID
    results, summaries = {}, []
    for name, tol in METHODS.items():
        res, summ = run_method(name, tol, offsets[name], base, out)
        results[name] = res
        summaries.append(summ)
    summary = pd.concat(summaries, ignore_index=True)

    # Steps 8-9 - benchmark table
    r10, r20 = results["Tolerance_10ns"], results["Tolerance_20ns"]
    key = ["TID", "Test_Case"]
    table = base[key + ["Source", "Rise_Min", "Fall_Min", "Limit_ns", "Report_Result"]].copy()
    table["Margin_orig_ns"] = (table[["Rise_Min", "Fall_Min"]].min(axis=1) - table["Limit_ns"]).round(3)
    grl = ["GRL_PHY.ALL.2"] if "GRL_PHY.ALL.2" in r10.columns else []
    table = table.merge(r10[key + ["Margin_ns", "Result"] + grl].rename(
        columns={"Margin_ns": "Margin_10_ns", "Result": "Result_10",
                 "GRL_PHY.ALL.2": "GRL_PHY.ALL.2_10"}), on=key, how="outer", indicator=True)
    table = table.merge(r20[key + ["Margin_ns", "Result"] + grl].rename(
        columns={"Margin_ns": "Margin_20_ns", "Result": "Result_20",
                 "GRL_PHY.ALL.2": "GRL_PHY.ALL.2_20"}), on=key, how="outer")
    unmatched = table[table["_merge"] != "both"]
    table = table.drop(columns="_merge")
    table["Change"] = [change_status(a, b) for a, b in zip(table["Result_10"], table["Result_20"])]
    low = table[["Rise_Min", "Fall_Min"]].min(axis=1)
    table["WorstCase_-10_Result"] = (low - 10 >= table["Limit_ns"]).map({True: "PASS", False: "FAIL"})
    table["WorstCase_-20_Result"] = (low - 20 >= table["Limit_ns"]).map({True: "PASS", False: "FAIL"})
    table["P_fail_10_%"] = [round(100 * fail_probability(v, l, 10), 1) for v, l in zip(low, table["Limit_ns"])]
    table["P_fail_20_%"] = [round(100 * fail_probability(v, l, 20), 1) for v, l in zip(low, table["Limit_ns"])]

    total = len(table)
    changed = int(table["Change"].str.contains("->").sum())
    summary["Changed_runs"] = changed
    summary["Changed_%"] = round(100 * changed / total, 2) if total else 0
    summary["WorstCase_FAIL"] = [int((table["WorstCase_-10_Result"] == "FAIL").sum()),
                                 int((table["WorstCase_-20_Result"] == "FAIL").sum())]
    summary["Expected_FAIL_random"] = [round(table["P_fail_10_%"].sum() / 100, 2),
                                       round(table["P_fail_20_%"].sum() / 100, 2)]
    categories = table["Change"].value_counts().rename_axis("Change").reset_index(name="Runs")

    bench = out / "Benchmark_Comparison"
    bench.mkdir()
    table.to_csv(bench / "Comparison_Table.csv", index=False)
    summary.to_csv(bench / "Comparison_Summary.csv", index=False)
    with pd.ExcelWriter(bench / "Comparison_Table.xlsx", engine="openpyxl") as xw:
        table.to_excel(xw, sheet_name="Comparison", index=False)
        summary.to_excel(xw, sheet_name="Summary", index=False)
        categories.to_excel(xw, sheet_name="Change_Categories", index=False)
        ref.to_excel(xw, sheet_name="Reference_Values")
        pd.DataFrame([{"Method": k, "Offset_ns": v, "Seed": args.seed} for k, v in offsets.items()]
                     ).to_excel(xw, sheet_name="Offsets", index=False)
        base.to_excel(xw, sheet_name="Measurements", index=False)
        vd_df.to_excel(xw, sheet_name="VD_PWR_Reports", index=False)
        if len(unmatched):
            unmatched.to_excel(xw, sheet_name="Unmatched", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(
                    60, max(10, max(len(str(c.value or "")) for c in col) + 2))

    # Step 10 - graphs
    bench_graphs(summary, table, offsets, bench / "Graphs")

    # Step 12 - final report
    borderline = table[(table["P_fail_20_%"] > 0) | (table["P_fail_10_%"] > 0)]
    vd_cols = [c for c in ["TID", "PHY.ALL.1", "PHY.ALL.2", "PHY.ALL.3", "Rise_Min", "Fall_Min",
                           "Run_Folder"] if c in vd_df.columns]
    obs = [
        f"{total} runs from {table['TID'].nunique()} DUTs; all {int((table['Report_Result'] == 'PASS').sum())} "
        f"originally PASS. Limit: Rise/Fall Min ≥ {config['limit_ns']:g} ns.",
        f"Offsets (seed {args.seed}): ±10 ns → {offsets['Tolerance_10ns']:+.3f} ns, "
        f"±20 ns → {offsets['Tolerance_20ns']:+.3f} ns.",
        f"With these offsets: ±10 ns {summary.loc[0, 'PASS']}/{total} PASS, "
        f"±20 ns {summary.loc[1, 'PASS']}/{total} PASS; {changed} run(s) changed result.",
        f"Worst case (-10 / -20 ns): {summary.loc[0, 'WorstCase_FAIL']} / "
        f"{summary.loc[1, 'WorstCase_FAIL']} run(s) FAIL.",
        f"Lowest original margin: {table['Margin_orig_ns'].min():.2f} ns "
        f"(TID {table.loc[table['Margin_orig_ns'].idxmin(), 'TID']}).",
    ]
    if len(unmatched):
        obs.append(f"{len(unmatched)} unmatched run(s) - see 'Unmatched' sheet.")
    rec = ("±10 ns never fails a run that passes today (lowest margin "
           f"{table['Margin_orig_ns'].min():.2f} ns > 10 ns). ±20 ns can fail the borderline DUTs "
           "listed below, but only for a negative offset, so a single random draw decides the "
           "outcome. Judge the methods on the worst-case and FAIL-chance columns, not on the "
           "single draw alone.") if table["Margin_orig_ns"].min() > 10 else (
           "Some runs have less than 10 ns margin; both tolerances can fail them - see the borderline table.")
    g = "Graphs/"
    tid_links = "".join(
        f"<li>TID {t}: <a href='../Tolerance_10ns/TID_{t}/Summary/summary.html'>±10 ns</a> · "
        f"<a href='../Tolerance_20ns/TID_{t}/Summary/summary.html'>±20 ns</a></li>"
        for t in sorted(table["TID"].unique()))
    body = (
        "<h2>Observations</h2><ul>" + "".join(f"<li>{html.escape(o)}</li>" for o in obs) + "</ul>"
        f"<h2>Recommendation</h2><p>{html.escape(rec)}</p>"
        "<h2>Summary</h2>" + html_table(summary)
        + "<h2>Change categories</h2>" + html_table(categories)
        + "<h2>Borderline runs</h2>"
        + (html_table(borderline[["TID", "Test_Case", "Rise_Min", "Fall_Min", "Margin_orig_ns",
                                  "Result_10", "Result_20", "WorstCase_-20_Result",
                                  "P_fail_10_%", "P_fail_20_%"]])
           if len(borderline) else "<p>None</p>")
        + "".join(f"<img src='{g}{n}.png'>" for n in
                  ["pass_fail_count", "pass_percentage", "changed_results", "dut_margin",
                   "fail_probability", "testcase_result"])
        + "<h2>Comparison table</h2>" + html_table(table)
        + "<h2>Per-TID reports</h2><ul>" + tid_links + "</ul>"
        + "<h2>Other measurements</h2><p><a href='../Other_Measurements/Summary.html'>"
          "Bit Rate, BIST duration, Hard_Reset, timing checks - tolerance comparison</a></p>"
        + "<h2>Reference values (all runs)</h2>" + html_table(ref.reset_index())
        + "<h2>VD PWR reports (Output\\VDPWR_EPR, after Corelation update)</h2>"
        + html_table(vd_df[vd_cols]))
    write_html(bench / "Comparison_Report.html", "Rise/Fall Time Tolerance Comparison (±10 ns vs ±20 ns)", body)

    # Step 11 - other measurements, each with its own limit
    om.run(out / "Other_Measurements", args.seed)

    print(f"Runs: {total}  DUTs: {table['TID'].nunique()}  offsets: {offsets}")
    print(summary.to_string(index=False))
    print(f"\nOutput: {out}")


if __name__ == "__main__":
    main()
