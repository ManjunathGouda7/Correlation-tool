"""
Write copies of the real GRL reports with adjusted Rise / Fall Time values.

For one C2 EPR run and one offset:
  - copies the report files of the run (no PDF - it cannot be edited):
        GRL_USB_PD_Report_*.html/.csv, Summary_Run_*.html/.csv,
        PDMerged\\PD_Merged.html, PDMerged\\PD_Merged.xml,
        PDMerged\\TEST.PD.PHY.ALL.2\\TEST.PD.PHY.ALL.2.html
  - replaces the 6 values (Rise/Fall Average/Minimum/Maximum) with value + offset
  - if an adjusted Minimum drops below the Minimum Limit:
        BMC_PHY_TX_EYE_5 -> FAIL, TEST.PD.PHY.ALL.2 -> FAIL,
        PASS / FAIL counts and PASS Rate updated (HTML, CSV, XML score)
  - adds a note at the top of every HTML report saying the values are adjusted.
"""

import re
import shutil
from pathlib import Path

import corelation as c

TC = "TEST.PD.PHY.ALL.2"
CHECK = "BMC_PHY_TX_EYE_5"
REPORT_FILES = ["GRL_USB_PD_Report_*.html", "GRL_USB_PD_Report_*.csv",
                "Summary_Run_*.html", "Summary_Run_*.csv",
                "PDMerged/PD_Merged.html", "PDMerged/PD_Merged.xml",
                f"PDMerged/{TC}/{TC}.html"]
LABELS = (("Average", "Avg"), ("Minimum", "Min"), ("Maximum", "Max"))
GREEN_PASS = re.compile(r'<d style="Color:Green;([^"]*)">PASS(\s*)</d>')


# ---------------------------------------------------------------- file io
def read_any(path: Path):
    """(text, encoding) - GRL CSVs are sometimes Windows-1252, not UTF-8."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1"), "latin-1"


def write_any(path: Path, text: str, enc: str):
    with open(path, "w", encoding=enc, newline="") as f:
        f.write(text)


# ---------------------------------------------------------------- values
def rewrite_values(text: str, values: dict) -> tuple:
    """Replace every Rise/Fall block's 6 values. values: {'Rise_Avg': 323.1, ...}.
    Returns (text, number of blocks changed)."""
    out, pos, blocks = [], 0, 0
    while True:
        start = text.find("Rise time:", pos)
        if start == -1:
            break
        cur, parts = start, []
        ok = True
        for kind, marker in (("Rise", "Rise time:"), ("Fall", "Fall time:")):
            cur = text.find(marker, cur)
            if cur == -1:
                ok = False
                break
            for label, short in LABELS:
                m = re.compile(label + r" value = ([\d.]+)").search(text, cur)
                if not m or m.start() - cur > 2000:
                    ok = False
                    break
                parts.append((m.start(1), m.end(1), f"{values[f'{kind}_{short}']:.6f}"))
                cur = m.end()
            if not ok:
                break
        if not ok:
            break
        for s, e, v in parts:
            out.append(text[pos:s])
            out.append(v)
            pos = e
        blocks += 1
    out.append(text[pos:])
    return "".join(out), blocks


# ---------------------------------------------------------------- HTML result
def fail_check_row(text: str) -> str:
    """BMC_PHY_TX_EYE_5 row: green PASS -> red FAIL."""
    for m in c.TR_RE.finditer(text):
        if CHECK in m.group(0):
            row = GREEN_PASS.sub(r'<d style="Color:Red;\1">FAIL\2</d>', m.group(0), 1)
            return text[:m.start()] + row + text[m.end():]
    return text


def html_test_status(text: str):
    row = c.find_summary_row(text, TC)
    return c.cell_text(c.TD_RE.findall(row.group(0))[-1]).upper() if row else None


def fail_test_html(text: str, log) -> str:
    """TEST.PD.PHY.ALL.2 PASS -> FAIL: summary row, detail header, counts."""
    moves = []
    det = c.find_detail_block(text, TC)
    if det:
        hdr = det[0]
        new_hdr = GREEN_PASS.sub(r'<d style="Color:Red;\1">FAIL\2</d>', hdr.group(0), 1)
        text = text[:hdr.start()] + new_hdr + text[hdr.end():]
    row = c.find_summary_row(text, TC)
    if row:
        last = list(c.TD_RE.finditer(row.group(0)))[-1]
        cell = (last.group(0).replace("LawnGreen", "Red")
                .replace("<b>PASS</b>", "<b>FAIL</b>"))
        new_row = row.group(0)[:last.start()] + cell + row.group(0)[last.end():]
        text = text[:row.start()] + new_row + text[row.end():]
        moves.append((c.moi_of(text, row.start()), "PASS", "FAIL"))
    if moves:
        text = c.adjust_counts(text, moves, log)
    return text


def add_note(text: str, note: str) -> str:
    banner = ('<div style="background:#fff3cd;border:1px solid #e0b84c;padding:6px 10px;'
              f'font:bold 13px Arial;margin:6px 0">{note}</div>')
    m = re.search(r"<body[^>]*>", text, re.I)
    return text[:m.end()] + banner + text[m.end():] if m else banner + text


# ---------------------------------------------------------------- CSV result
def fail_csv(text: str) -> str:
    """Result PASS -> FAIL for the check and the test rows, counts updated."""
    lines = text.splitlines(keepends=True)
    test_changed = False
    for i, ln in enumerate(lines):
        cells = ln.rstrip("\r\n").split(",")
        names = [x.strip() for x in cells]
        if any(n == CHECK or n.startswith(TC + " ") or n == TC for n in names) \
                and "PASS" in names and not any("#" in n for n in names):
            k = names.index("PASS")
            cells[k] = cells[k].replace("PASS", "FAIL")
            eol = ln[len(ln.rstrip("\r\n")):]
            lines[i] = ",".join(cells) + eol
            if CHECK not in names:
                test_changed = True
    if test_changed:
        lines = adjust_csv_counts(lines)
    return "".join(lines)


def adjust_csv_counts(lines):
    """Count rows below a header with PASS and FAIL columns: PASS-1, FAIL+1."""
    for i, ln in enumerate(lines):
        head = [h.strip().upper() for h in ln.rstrip("\r\n").split(",")]
        if "PASS" not in head or "FAIL" not in head:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        vals = lines[j].rstrip("\r\n").split(",")
        try:
            p, f = head.index("PASS"), head.index("FAIL")
            vals[p] = str(int(vals[p]) - 1)
            vals[f] = str(int(vals[f]) + 1)
            rate = next((k for k, h in enumerate(head) if h.startswith("PASS RATE")), None)
            if rate is not None:
                vals[rate] = c.pass_rate(int(vals[p]), int(vals[f]))
        except (ValueError, IndexError):
            continue
        eol = lines[j][len(lines[j].rstrip("\r\n")):]
        lines[j] = ",".join(vals) + eol
    return lines


# ---------------------------------------------------------------- XML result
def fail_xml(text: str) -> str:
    m = c.TEST_BLOCK_RES[TC].search(text)
    if not m:
        return text
    block = c.SCORE_RE.sub(lambda s: '<score value="fail"', m.group(0), 1)
    return text[:m.start()] + block + text[m.end():]


# ---------------------------------------------------------------- driver
def write_run(run_dir: Path, dest: Path, original: dict, offset: float, limit: float,
              method: str, log=print) -> dict:
    """Copy + rewrite the reports of one run. Returns a small status dict."""
    adjusted = {k: original[k] + offset for k in original}
    eye_fail = min(adjusted["Rise_Min"], adjusted["Fall_Min"]) < limit
    note = (f"Tolerance study {method}: Rise/Fall Time values are the measured values "
            f"{offset:+.6f} ns. BMC_PHY_TX_EYE_5 re-evaluated against the Minimum Limit "
            f"{limit:g} ns. Not an original GRL report.")
    files = sorted({f for pat in REPORT_FILES for f in run_dir.glob(pat)})
    status = {"files": 0, "blocks": 0, "eye_fail": eye_fail, "test_was": None}
    for src in files:
        rel = src.relative_to(run_dir)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        text, enc = read_any(src)
        text, n = rewrite_values(text, adjusted)
        status["blocks"] += n
        suffix = src.suffix.lower()
        if suffix == ".html":
            status["test_was"] = status["test_was"] or html_test_status(text)
        if eye_fail:
            if suffix == ".html":
                text = fail_check_row(text)
                if html_test_status(text) == "PASS":
                    text = fail_test_html(text, lambda *_: None)
            elif suffix == ".csv":
                text = fail_csv(text)
            elif suffix == ".xml":
                text = fail_xml(text)
        if suffix == ".html":
            text = add_note(text, note)
        write_any(out, text, enc)
        status["files"] += 1
    # keep the eye diagram images next to the reports
    eye = run_dir / "PDMerged" / "EyeDiagram Images"
    if eye.is_dir():
        shutil.copytree(eye, dest / "PDMerged" / "EyeDiagram Images", dirs_exist_ok=True)
    return status
