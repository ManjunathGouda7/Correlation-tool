# Corelation – VD PWR EPR report update

**Input:** VD PWR run folder, C2 EPR run folder, output folder.
The VD PWR folder is copied to `<output>\VDPWR_EPR\` and all changes are made on that copy.

## Steps

1. **Clean trace files** – `PDMerged\TEST.PD.PHY.ALL.1` to `.5`
   - Open each `.grltrace` (7-Zip) and delete `testresult.deb`.

2. **Delete reports** – remove all `.pdf` and `.csv` files from the run folder and `PDMerged`.

3. **Update XML** – `PDMerged\PD_Merged.xml`, test cases `TEST.PD.PHY.ALL.1` to `.3`
   - Copy the C2 EPR results so the test, condition and check scores become `pass`.
   - Keep VD PWR's own packet comments (e.g. Packet63 / Packet73).
   - Original is saved as `PD_Merged.xml.bak`.

## Run

- UI: `python corelation_ui.py`
- Command line: `python corelation.py "<VD PWR folder>" --c2 "<C2 EPR folder>"` (add `--dry-run` to preview)
