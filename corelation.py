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

Usage:
    python corelation.py "D:\\Corelation\\VDPWR_EPR" --c2 "D:\\Corelation\\C2_EPR"
    python corelation.py "<folder1>" "<folder2>" --c2 "<c2 folder>" --dry-run
    python corelation.py "<folder>" --steps 1,2      # only steps 1 and 2
"""

import argparse
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


# ---------------------------------------------------------------- driver
def process(folders, c2, steps, dry_run=False, log=print):
    """Run the selected steps on every PDMerged folder under `folders`."""
    c2_blocks, c2_run = None, None
    if "3" in steps:
        c2_xml = find_c2_xml(Path(c2))
        c2_run = c2_xml.parent.parent.resolve()
        c2_blocks = load_c2_blocks(c2_xml)
        log(f"C2 EPR source: {c2_xml}")

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
            if c2_run and pdmerged.parent.resolve() == c2_run:
                log(f"\nSkipping C2 EPR source run: {pdmerged}")
                continue
            if "1" in steps:
                step1_remove_testresult(pdmerged, seven_zip, dry_run, log)
            if "2" in steps:
                step2_delete_reports(pdmerged, dry_run, log)
            if "3" in steps:
                step3_copy_c2_results(pdmerged, c2_blocks, dry_run, log)

    log("\nDone." + (" (dry run - nothing changed)" if dry_run else ""))


def main():
    parser = argparse.ArgumentParser(description="Corelation report preparation")
    parser.add_argument("folders", nargs="*",
                        help="Input folder(s); every PDMerged folder below is processed")
    parser.add_argument("--steps", default="1,2,3",
                        help="Comma separated steps to run (default: 1,2,3)")
    parser.add_argument("--c2",
                        help="C2 EPR run folder (or its PD_Merged.xml) used by step 3")
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
    if "3" in steps:
        c2 = args.c2 or input("Enter C2 EPR folder path: ").strip().strip('"')
        if not c2:
            sys.exit("Step 3 needs the C2 EPR folder (--c2).")
    try:
        process(folders, c2, steps, args.dry_run)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
