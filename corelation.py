"""
Corelation report preparation tool.

Step 1: For every PDMerged folder found under the input folder(s), open the
        .grltrace files of TEST.PD.PHY.ALL.1 .. TEST.PD.PHY.ALL.5 and delete
        'testresult.deb' from inside the archive.
Step 2: Delete the PDF and CSV report files (run folder + PDMerged folder,
        top level only - test-case sub folders are not touched).
Step 3: In PDMerged\\PD_Merged.xml, replace the <test> blocks of
        TEST.PD.PHY.ALL.1 .. TEST.PD.PHY.ALL.3 with the ones from the C2 EPR
        run's PD_Merged.xml, keeping the target's own non-empty check
        comments (packet numbers). A backup (PD_Merged.xml.bak) is written first.
Step 4: Update the HTML reports (TEST.PD.PHY.ALL.1 .. 3 per-test HTML,
        PD_Merged.html, GRL_USB_PD_Report_*.html, Summary_Run_*.html) to match
        the updated XML: result + description rows are copied from the C2 EPR
        HTML, rows with a packet number keep the VD PWR packet number, summary
        counts and PASS Rate are adjusted.
Step 5: Delete PDMerged\\AMS_Run_*.html.

Usage:
    python corelation.py "D:\\Corelation\\VDPWR_EPR" --c2 "D:\\Corelation\\C2_EPR"
    python corelation.py "<folder1>" "<folder2>" --c2 "<c2 folder>" --dry-run
    python corelation.py "<folder>" --steps 1,2      # only steps 1 and 2
"""

import argparse
import html
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PDMERGED = "PDMerged"
PHY_TEST_CASES = [f"TEST.PD.PHY.ALL.{i}" for i in range(1, 6)]
XML_TEST_CASES = [f"TEST.PD.PHY.ALL.{i}" for i in range(1, 4)]
MERGED_XML = "PD_Merged.xml"
ENTRY_TO_DELETE = "testresult.deb"
REPORT_EXTENSIONS = {".pdf", ".csv"}
C2_STEPS = {"3", "4"}  # steps that need the C2 EPR run

SEVEN_ZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]
# hide the console window 7z.exe would otherwise flash when run from the UI
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TEST_BLOCK_RES = {
    tc: re.compile(r'<test tcID="' + re.escape(tc) + r'">.*?</test>', re.DOTALL)
    for tc in XML_TEST_CASES
}  # <test> elements are not nested, so the first </test> closes the block
SCORE_RE = re.compile(r'<score value="([^"]*)"')
CHECK_RE = re.compile(r'(<check checkID="([^"]+)">.*?<comment>)(.*?)(</comment>)',
                      re.DOTALL)

# HTML reports (steps 4 and 5)
MERGED_HTML = "PD_Merged.html"
RUN_REPORT_GLOBS = ("GRL_USB_PD_Report_*.html", "Summary_Run_*.html")  # in the run folder
AMS_GLOB = "AMS_Run_*.html"
TR_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL)
TD_RE = re.compile(r"<td\b.*?</td>", re.DOTALL)
TH_RE = re.compile(r"<th\b.*?</th>", re.DOTALL)
H1_RE = re.compile(r"<h1\b.*?</h1>", re.DOTALL)
DETAIL_HEADER_MARK = "background-color:#031B6130;"  # first row of a test's detail block
PACKET_RE = re.compile(r"\bPacket\d+\b")


# ---------------------------------------------------------------- helpers
def find_7zip():
    for path in SEVEN_ZIP_CANDIDATES:
        if os.path.isfile(path):
            return path
    return shutil.which("7z")


def find_pdmerged_folders(root: Path):
    """Every PDMerged folder under root (or root itself).
    Does not descend into PDMerged folders - they hold hundreds of test dirs."""
    if root.name.lower() == PDMERGED.lower():
        return [root]
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        for d in [d for d in dirnames if d.lower() == PDMERGED.lower()]:
            found.append(Path(dirpath, d))
            dirnames.remove(d)
    return sorted(found)


def is_report_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in REPORT_EXTENSIONS


def copy_run(src: Path, dst: Path, skip_reports=False, log=print, workers=8):
    """Copy a run folder tree in parallel.
    skip_reports=True leaves out the PDF/CSV files step 2 would delete anyway."""
    jobs, skipped = [], 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        report_level = (Path(dirpath).name.lower() == PDMERGED.lower()
                        or any(d.lower() == PDMERGED.lower() for d in dirnames))
        for name in filenames:
            if skip_reports and report_level and is_report_file(name):
                skipped += 1
                continue
            jobs.append((Path(dirpath, name), target_dir / name))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # list() re-raises the first copy error, if any
        list(pool.map(lambda job: shutil.copy2(*job), jobs))
    log(f"Copied {len(jobs)} files"
        + (f" (left out {skipped} PDF/CSV report files)" if skipped else ""))


def read_text(path: Path) -> str:
    # newline="" keeps the original line endings untouched
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: Path, text: str):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ---------------------------------------------------------------- Step 1
def delete_entry_with_7zip(seven_zip: str, trace: Path, entry: str):
    result = subprocess.run([seven_zip, "d", str(trace), entry, "-y"],
                            capture_output=True, text=True,
                            creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip())


def delete_entry_with_python(trace: Path, entry: str):
    """Rewrite the zip without the entry (fallback when 7-Zip is missing)."""
    fd, tmp_name = tempfile.mkstemp(suffix=".grltrace", dir=trace.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(trace) as src, \
                zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename.lower() != entry.lower():
                    with src.open(item) as fin, dst.open(item, "w") as fout:
                        shutil.copyfileobj(fin, fout, 1 << 20)  # stream, 1 MB chunks
        os.replace(tmp_name, trace)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def step1_remove_testresult(pdmerged: Path, seven_zip, dry_run=False, log=print):
    log(f"\n[Step 1] {pdmerged}")
    for case in PHY_TEST_CASES:
        case_dir = pdmerged / case
        traces = sorted(case_dir.glob("*.grltrace")) if case_dir.is_dir() else []
        if not traces:
            log(f"  {case}: no .grltrace file - skipped")
            continue
        for trace in traces:
            try:
                with zipfile.ZipFile(trace) as zf:
                    match = next((n for n in zf.namelist()
                                  if n.lower() == ENTRY_TO_DELETE), None)
                if match is None:
                    log(f"  {trace.name}: {ENTRY_TO_DELETE} not present - nothing to do")
                elif dry_run:
                    log(f"  {trace.name}: would delete {match}")
                else:
                    if seven_zip:
                        delete_entry_with_7zip(seven_zip, trace, match)
                    else:
                        delete_entry_with_python(trace, match)
                    log(f"  {trace.name}: deleted {match}")
            except zipfile.BadZipFile:
                log(f"  {trace.name}: not a valid archive - skipped")
            except Exception as e:  # one bad file must not stop the others
                log(f"  {trace.name}: ERROR {e}")


# ---------------------------------------------------------------- Step 2
def step2_delete_reports(pdmerged: Path, dry_run=False, log=print):
    run_folder = pdmerged.parent
    log(f"\n[Step 2] {run_folder}")
    files = [f for folder in (run_folder, pdmerged) for f in sorted(folder.iterdir())
             if f.is_file() and is_report_file(f.name)]
    if not files:
        log("  no PDF/CSV files found")
    for f in files:
        rel = f.relative_to(run_folder)
        if dry_run:
            log(f"  would delete {rel}")
        else:
            f.unlink()
            log(f"  deleted {rel}")


# ---------------------------------------------------------------- Step 3
def block_scores(block: str):
    return [s.strip() for s in SCORE_RE.findall(block)]


def keep_target_comments(c2_block: str, target_block: str) -> str:
    """Use the C2 block but keep the target's non-empty check comments
    (e.g. its own Packet numbers)."""
    target_comments = {m.group(2): m.group(3) for m in CHECK_RE.finditer(target_block)
                       if m.group(3).strip()}
    return CHECK_RE.sub(
        lambda m: m.group(1) + target_comments.get(m.group(2), m.group(3)) + m.group(4),
        c2_block)


def find_c2_xml(c2_path: Path) -> Path:
    if c2_path.is_file():
        return c2_path
    candidates = [p / MERGED_XML for p in find_pdmerged_folders(c2_path)]
    candidates = [c for c in candidates if c.is_file()]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one {PDMERGED}\\{MERGED_XML} under "
                         f"{c2_path}, found {len(candidates)}: {candidates}")
    return candidates[0]


TID_RE = re.compile(r"TID[ _]*(\d+)", re.I)
RUN_TID_RE = re.compile(r"(?<!\d)(\d{5})_", re.I)  # "16394_PD_..." run folder names


def tid_of(path: Path):
    """DUT id from a path: last 'TID xxxxx' folder, else a '16394_...' folder name."""
    found = TID_RE.findall(str(path))
    if found:
        return found[-1]
    found = RUN_TID_RE.findall(str(path))
    return found[-1] if found else None


def c2_run_index(c2_root: Path, log=print) -> dict:
    """{tid: [(pdmerged, blocks), ...]} for every C2 EPR run under c2_root.
    Copies of another DUT nested inside a TID folder (TID 16557\\TID 16502\\..)
    are left out so a run is not counted twice."""
    index = {}
    for pdm in find_pdmerged_folders(c2_root):
        rel = pdm.relative_to(c2_root) if pdm != c2_root else Path(pdm.name)
        if len(TID_RE.findall(str(rel))) > 1:
            log(f"  ignoring nested copy: {rel}")
            continue
        xml = pdm / MERGED_XML
        tid = tid_of(pdm)
        if tid and xml.is_file():
            index.setdefault(tid, []).append((pdm, load_c2_blocks(xml)))
    return index


def c2_plan(runs) -> tuple:
    """Pick the C2 source for each PHY test case of one DUT.
    Main source = the 'PD' run (not Flip / Updated) that has all test cases.
    A test case that does not pass there is taken from another non-Flip run
    (e.g. '..._PD_Updated') where it passes.
    Returns ([(pdmerged, {tc: block})...], problem text or None)."""
    def name(pdm):
        return str(pdm).lower()
    not_flip = [r for r in runs if "flip" not in name(r[0])]
    main = [r for r in not_flip if "updated" not in name(r[0])
            and all(tc in r[1] for tc in XML_TEST_CASES)]
    if len(main) != 1:
        return [], f"expected one C2 EPR PD run with {XML_TEST_CASES[0]}..3, found {len(main)}"
    main_pdm, main_blocks = main[0]
    plan = {main_pdm: dict(main_blocks)}
    for tc, block in main_blocks.items():
        if block_scores(block)[0].lower() == "pass":
            continue
        for pdm, blocks in not_flip:
            if pdm != main_pdm and tc in blocks and \
                    block_scores(blocks[tc])[0].lower() == "pass":
                del plan[main_pdm][tc]
                plan.setdefault(pdm, {})[tc] = blocks[tc]
                break
    return list(plan.items()), None


def load_c2_blocks(c2_xml: Path) -> dict:
    """Read the C2 EPR xml once and keep only the test blocks we need."""
    text = read_text(c2_xml)
    blocks = {}
    for tc, pattern in TEST_BLOCK_RES.items():
        m = pattern.search(text)
        if m:
            blocks[tc] = m.group(0)
    return blocks


def step3_copy_c2_results(pdmerged: Path, c2_blocks: dict, dry_run=False, log=print):
    target = pdmerged / MERGED_XML
    log(f"\n[Step 3] {target}")
    if not target.is_file():
        log(f"  {MERGED_XML} not found - skipped")
        return

    text = read_text(target)
    changed = False
    for tc, pattern in TEST_BLOCK_RES.items():
        src = c2_blocks.get(tc)
        dst = pattern.search(text)
        if src is None:
            log(f"  {tc}: not in C2 EPR xml - skipped")
            continue
        if dst is None:
            log(f"  {tc}: not in target xml - skipped")
            continue
        new_block = keep_target_comments(src, dst.group(0))
        if new_block == dst.group(0):
            log(f"  {tc}: already done")
            continue
        log(f"  {tc}: {block_scores(dst.group(0))} -> {block_scores(new_block)}")
        text = text[:dst.start()] + new_block + text[dst.end():]
        changed = True

    if changed and not dry_run:
        backup = target.with_name(target.name + ".bak")
        if not backup.exists():  # keep the very first original
            shutil.copy2(target, backup)
            log(f"  backup: {backup.name}")
        write_text(target, text)
        log("  saved")


# ---------------------------------------------------------------- HTML helpers
def cell_text(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment.replace("&nbsp", " ")))
    return re.sub(r"\s+", " ", text).strip()


def tc_pattern(tc: str):
    # \b stops TEST.PD.PHY.ALL.1 from matching TEST.PD.PHY.ALL.10
    return re.compile(re.escape(tc) + r"\b")


def find_summary_row(text: str, tc: str):
    """Row of the 'Result Summary' table: <td><p><b>TEST.ID</b></p></td> ... result."""
    cell = f"<b>{tc}</b>"
    for m in TR_RE.finditer(text):
        if cell in m.group(0):
            return m
    return None


def find_detail_block(text: str, tc: str):
    """(header row match, body start, body end) of a test's detail rows."""
    pat = tc_pattern(tc)
    for m in TR_RE.finditer(text):
        row = m.group(0)
        if DETAIL_HEADER_MARK in row and pat.search(cell_text(row)):
            end = text.find("</table>", m.end())
            nxt = text.find(DETAIL_HEADER_MARK, m.end(), end)
            if nxt != -1:
                end = text.rfind("<tr", m.end(), nxt)
            return m, m.end(), end
    return None


def merge_packet_rows(c2_body: str, target_body: str):
    """C2 detail rows, but rows naming a packet keep the target's packet row.
    Packet rows are paired in order per label ('SourceCap', 'Request', '')."""
    target_rows = {}
    for row in TR_RE.findall(target_body):
        text = cell_text(row)
        if PACKET_RE.search(text):
            target_rows.setdefault(PACKET_RE.sub("", text).strip(), []).append(row)
    kept_c2 = []

    def swap(m):
        text = cell_text(m.group(0))
        if not PACKET_RE.search(text):
            return m.group(0)
        rows = target_rows.get(PACKET_RE.sub("", text).strip())
        if rows:
            return rows.pop(0)
        kept_c2.append(text)  # target has no packet for it -> C2 value stays
        return m.group(0)

    return TR_RE.sub(swap, c2_body), kept_c2


def table_after(text: str, heading: str):
    """(start, end) of the first <table> after the <h1> containing heading."""
    for h in H1_RE.finditer(text):
        if heading in cell_text(h.group(0)):
            start = text.find("<table", h.end())
            end = text.find("</table>", start)
            if start != -1 and end != -1:
                return start, end
    return None


def moi_of(text: str, pos: int) -> str:
    """MOI name from the '<MOI>- Result Summary' heading above pos."""
    name = ""
    for h in H1_RE.finditer(text, 0, pos):
        t = cell_text(h.group(0))
        if "Result Summary" in t:
            name = t.split("- Result Summary")[0].strip()
    return name


def adjust_counts(text: str, moves, log) -> str:
    """moves: list of (moi, old_status, new_status). Update 'Test Summary - Overall'
    and 'Test Summary - All MOI' counts and PASS Rate."""
    def fix_row(row: str, headers, label, delta):
        cells = list(TD_RE.finditer(row))
        values = [cell_text(c.group(0)) for c in cells]
        idx = {h.upper(): i for i, h in enumerate(headers)}
        try:
            p, f = idx["PASS"], idx["FAIL"]
            rate = next(i for h, i in idx.items() if h.startswith("PASS RATE"))
            old_rate_ok = values[rate] == pass_rate(int(values[p]), int(values[f]))
            for status, d in delta.items():
                i = idx[status.upper()]
                values[i] = str(int(values[i]) + d)
        except (KeyError, StopIteration, ValueError) as e:
            log(f"    {label}: counts not updated ({e})")
            return row
        if old_rate_ok:
            values[rate] = pass_rate(int(values[p]), int(values[f]))
        else:
            log(f"    {label}: PASS Rate left as is (unknown formula)")
        out, last = [], 0
        for c, v in zip(cells, values):
            old = cell_text(c.group(0))
            out.append(row[last:c.start()])
            out.append(c.group(0) if old == v else
                       c.group(0).replace(f"<b>{old}</b>", f"<b>{v}</b>", 1))
            last = c.end()
        out.append(row[last:])
        return "".join(out)

    def delta_for(items):
        d = {}
        for _, old, new in items:
            d[old] = d.get(old, 0) - 1
            d[new] = d.get(new, 0) + 1
        return {k: v for k, v in d.items() if v}

    for heading, per_moi in (("Test Summary - Overall", False),
                             ("Test Summary - All MOI", True)):
        span = table_after(text, heading)
        if not span:
            continue
        table = text[span[0]:span[1]]
        headers = [cell_text(h) for h in TH_RE.findall(table)]
        new_table, last = [], 0
        for m in TR_RE.finditer(table):
            row = m.group(0)
            if "<td" not in row:
                continue
            if per_moi:
                moi = cell_text(TD_RE.search(row).group(0))
                delta = delta_for([mv for mv in moves if mv[0] == moi])
            else:
                moi, delta = "Overall", delta_for(moves)
            if not delta:
                continue
            new_table += [table[last:m.start()], fix_row(row, headers, moi, delta)]
            last = m.end()
            log(f"    counts {moi}: {delta}")
        new_table.append(table[last:])
        text = text[:span[0]] + "".join(new_table) + text[span[1]:]
    return text


def pass_rate(passed: int, failed: int) -> str:
    # the GRL report rounds down: 118 / (118 + 4) = 96.7 -> 96%
    total = passed + failed
    return f"{math.floor(passed * 100 / total) if total else 0}%"


def update_html(target: Path, c2_file: Path, xml_status: dict, dry_run, log,
                test_cases=XML_TEST_CASES):
    """Copy result + description of the XML test cases from c2_file into target."""
    rel = target.name
    if not c2_file or not c2_file.is_file():
        log(f"  {rel}: no matching C2 EPR file - skipped")
        return
    text, c2 = read_text(target), read_text(c2_file)
    moves, changed = [], False
    for tc in test_cases:
        row, c2_row = find_summary_row(text, tc), find_summary_row(c2, tc)
        if not row:
            continue  # test not in this report
        if not c2_row:
            log(f"  {rel} {tc}: not in C2 EPR report - skipped")
            continue
        old_td = TD_RE.findall(row.group(0))[-1]  # last cell = Test Result
        new_td = TD_RE.findall(c2_row.group(0))[-1]
        old, new = cell_text(old_td), cell_text(new_td)
        if xml_status.get(tc, "").upper() != new.upper():
            log(f"  {rel} {tc}: XML says '{xml_status.get(tc)}', C2 HTML says "
                f"'{new}' - skipped (run step 3 first)")
            continue

        # detail block first (it is after the summary row, so offsets stay valid)
        det, c2_det = find_detail_block(text, tc), find_detail_block(c2, tc)
        if det and c2_det:
            hdr, b0, b1 = det
            c2_hdr, c0, c1 = c2_det
            body, kept_c2 = merge_packet_rows(c2[c0:c1], text[b0:b1])
            hdr_tds = TD_RE.findall(hdr.group(0))
            new_hdr = hdr.group(0).replace(hdr_tds[0], TD_RE.findall(c2_hdr.group(0))[0], 1)
            if new_hdr + body != text[hdr.start():b1]:
                text = text[:hdr.start()] + new_hdr + body + text[b1:]
                changed = True
            for k in kept_c2:
                log(f"  {rel} {tc}: no VD PWR packet for '{k}' - C2 value kept")
        elif det or c2_det:  # e.g. Summary_Run has no detail rows at all
            log(f"  {rel} {tc}: detail rows not found - only the result is updated")

        if old != new:
            row = find_summary_row(text, tc)
            last = list(TD_RE.finditer(row.group(0)))[-1]
            new_row = row.group(0)[:last.start()] + new_td + row.group(0)[last.end():]
            text = text[:row.start()] + new_row + text[row.end():]
            moves.append((moi_of(text, row.start()), old, new))
            changed = True
        log(f"  {rel} {tc}: {old} -> {new}" if old != new else f"  {rel} {tc}: {new}")

    if moves:
        text = adjust_counts(text, moves, log)
    if not changed:
        log(f"  {rel}: already up to date")
    elif not dry_run:
        backup = target.with_name(target.name + ".bak")
        if not backup.exists():
            shutil.copy2(target, backup)
        write_text(target, text)
        log(f"  {rel}: saved")


def xml_test_status(xml: Path) -> dict:
    text = read_text(xml) if xml.is_file() else ""
    out = {}
    for tc, pattern in TEST_BLOCK_RES.items():
        m = pattern.search(text)
        if m:
            out[tc] = block_scores(m.group(0))[0]
    return out


# ---------------------------------------------------------------- Step 4
def step4_update_html(pdmerged: Path, c2_pdmerged: Path, dry_run=False, log=print,
                      test_cases=XML_TEST_CASES):
    log(f"\n[Step 4] {pdmerged.parent}"
        + (f"  ({', '.join(test_cases)})" if list(test_cases) != XML_TEST_CASES else ""))
    xml_status = xml_test_status(pdmerged / MERGED_XML)
    if not xml_status:
        log(f"  {MERGED_XML} missing or has no {XML_TEST_CASES[0]}.. - skipped")
        return
    pairs = [(pdmerged / tc / f"{tc}.html", c2_pdmerged / tc / f"{tc}.html")
             for tc in test_cases]
    pairs.append((pdmerged / MERGED_HTML, c2_pdmerged / MERGED_HTML))
    for pattern in RUN_REPORT_GLOBS:
        c2_reports = sorted(c2_pdmerged.parent.glob(pattern))
        for report in sorted(pdmerged.parent.glob(pattern)):
            same_name = c2_pdmerged.parent / report.name
            pairs.append((report, same_name if same_name.is_file()
                          else (c2_reports[0] if len(c2_reports) == 1 else None)))
    for target, c2_file in pairs:
        if target.is_file():
            update_html(target, c2_file, xml_status, dry_run, log, test_cases)


# ---------------------------------------------------------------- Step 5
def step5_delete_ams(pdmerged: Path, dry_run=False, log=print):
    log(f"\n[Step 5] {pdmerged}")
    ams_files = sorted(pdmerged.glob(AMS_GLOB))
    if not ams_files:
        log("  no AMS HTML file - nothing to delete")
    for f in ams_files:
        if dry_run:
            log(f"  would delete {f.name}")
        else:
            f.unlink()
            log(f"  deleted {f.name}")


# ---------------------------------------------------------------- driver
def process(folders, c2, steps, dry_run=False, log=print):
    """Run the selected steps on every PDMerged folder under `folders`.
    c2 = one C2 EPR run (used for every target), or a folder with many C2 EPR
    runs: each target is then paired with the C2 run of the same TID."""
    c2_index, c2_runs = None, set()   # by-TID mode
    single = None                     # [(c2_pdmerged, blocks)] in single-run mode
    if steps & C2_STEPS:
        c2_path = Path(c2)
        xmls = ([c2_path] if c2_path.is_file() else
                [p / MERGED_XML for p in find_pdmerged_folders(c2_path)
                 if (p / MERGED_XML).is_file()])
        if not xmls:
            raise ValueError(f"No {PDMERGED}\\{MERGED_XML} under {c2_path}")
        if len(xmls) == 1:
            single = [(xmls[0].parent, load_c2_blocks(xmls[0]))]
            log(f"C2 EPR source: {xmls[0]}")
        else:
            log(f"C2 EPR folder with {len(xmls)} runs - pairing by TID: {c2_path}")
            c2_index = c2_run_index(c2_path, log)
            log(f"  C2 EPR DUTs: {', '.join(sorted(c2_index))}")
        c2_runs = {x.parent.parent.resolve() for x in xmls}

    seven_zip = None
    if "1" in steps:
        seven_zip = find_7zip()
        log("Using " + (f"7-Zip: {seven_zip}" if seven_zip
                        else "Python zipfile (7-Zip not found)"))

    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            log(f"\nFolder not found: {root}")
            continue
        pdmerged_list = find_pdmerged_folders(root)
        if not pdmerged_list:
            log(f"\nNo {PDMERGED} folder under: {root}")
            continue
        for pdmerged in pdmerged_list:
            if pdmerged.parent.resolve() in c2_runs:
                log(f"\nSkipping C2 EPR source run: {pdmerged}")
                continue
            plan = single
            if c2_index is not None:
                tid = tid_of(pdmerged)
                log(f"\n======== TID {tid}: {pdmerged.parent}")
                plan, problem = c2_plan(c2_index.get(tid, []))
                if problem:
                    log(f"  C2 EPR: {problem} - steps 3-4 skipped")
                for c2_pdm, blocks in plan:
                    log(f"  C2 EPR source for {', '.join(sorted(blocks))}: {c2_pdm.parent}")
            if "1" in steps:
                step1_remove_testresult(pdmerged, seven_zip, dry_run, log)
            if "2" in steps:
                step2_delete_reports(pdmerged, dry_run, log)
            for c2_pdm, blocks in (plan or []):
                if "3" in steps:
                    step3_copy_c2_results(pdmerged, blocks, dry_run, log)
                if "4" in steps:
                    step4_update_html(pdmerged, c2_pdm, dry_run, log,
                                      [tc for tc in XML_TEST_CASES if tc in blocks])
            if "5" in steps:
                step5_delete_ams(pdmerged, dry_run, log)

    log("\nDone." + (" (dry run - nothing changed)" if dry_run else ""))


def main():
    parser = argparse.ArgumentParser(description="Corelation report preparation")
    parser.add_argument("folders", nargs="*",
                        help="Input folder(s); every PDMerged folder below is processed")
    parser.add_argument("--steps", default="1,2,3,4,5",
                        help="Comma separated steps to run (default: 1,2,3,4,5)")
    parser.add_argument("--c2",
                        help="C2 EPR run folder (or its PD_Merged.xml) used by steps 3-4")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only show what would be changed")
    args = parser.parse_args()

    folders = args.folders
    if not folders:
        entered = input("Enter input folder path(s), separated by ';': ").strip()
        folders = [f.strip().strip('"') for f in entered.split(";") if f.strip()]
    if not folders:
        sys.exit("No input folder given.")

    steps = {s.strip() for s in args.steps.split(",")}
    c2 = None
    if steps & C2_STEPS:
        c2 = args.c2 or input("Enter C2 EPR folder path: ").strip().strip('"')
        if not c2:
            sys.exit("Steps 3-4 need the C2 EPR folder (--c2).")
    try:
        process(folders, c2, steps, args.dry_run)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
