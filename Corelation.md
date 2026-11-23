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

4. **Update HTML** – the per-test HTML of `TEST.PD.PHY.ALL.1` to `.3`, `PD_Merged.html`, `GRL_USB_PD_Report_*.html`, `Summary_Run_*.html`
   - Result and description rows are copied from the C2 EPR HTML (only where the updated XML has the same result).
   - Rows with a packet number keep the VD PWR packet number.
   - PASS / INCOMPLETE counts and PASS Rate in the summary tables are adjusted.
   - Originals are saved as `.bak`.

5. **Delete AMS** – delete `PDMerged\AMS_Run_*.html`.

## Run

- UI: `python corelation_ui.py`
- Command line: `python corelation.py "<VD PWR folder>" --c2 "<C2 EPR folder>"` (add `--dry-run` to preview)

## Many DUTs at once

The C2 EPR folder may hold the runs of many DUTs (e.g. `D:\Corelation\C2_EPR` with `TID 16394\...`).
Each VD PWR run is then paired with the C2 EPR run of the same TID:
- the `..._PD_...` run (not Flip / Updated) that has TEST.PD.PHY.ALL.1-3;
- a test case that does not pass there is taken from another non-Flip run where it passes
  (TID 16502: PHY.ALL.2 from `16502_PD_Updated`);
- copies of another DUT nested in a TID folder (`TID 16557\TID 16502`) are ignored;
- a DUT with no C2 EPR PD run gets steps 1, 2, 5 only (logged as "steps 3-4 skipped").

Example: `python corelation.py "D:\Corelation\Output\VDPWR_EPR" --c2 "D:\Corelation\C2_EPR"`
(the UI does the same when the VD PWR input is the whole `VDPWR_EPR` folder).

## Tolerance comparison (±10 ns vs ±20 ns Rise/Fall Time)

`python tolerance_compare.py [--seed N] [--offset10 X] [--offset20 Y]`  → `Comparison_Output\`

- One random offset per method (fixed seed, saved in `config.json`) is added to all 6 values
  (Rise/Fall Average/Minimum/Maximum) of every run; `--offset10/--offset20` force a value (e.g. worst case -10 / -20).
- A run passes when adjusted Rise Min and Fall Min are ≥ the Minimum Limit (300 ns).
- `Tolerance_10ns\` / `Tolerance_20ns\`: `Overall_Summary.html`, and per DUT `TID_xxxxx\`
  with `Reports\`, `Plots\`, `Summary\` and `GRL_Reports\`.
- `GRL_Reports\` = copies of the real C2 EPR GRL reports (GRL_USB_PD_Report, Summary_Run, PD_Merged html/xml,
  TEST.PD.PHY.ALL.2.html, CSVs; no PDF) with the adjusted values written in (`grl_report_writer.py`).
  If a run drops below the limit: BMC_PHY_TX_EYE_5 and TEST.PD.PHY.ALL.2 become FAIL and the PASS/FAIL counts
  and PASS Rate are updated. Each HTML gets a yellow note that the values are adjusted.
- `Benchmark_Comparison\`: Comparison_Table.xlsx/.csv, Comparison_Summary.csv, Comparison_Report.html, Graphs\.
- `Other_Measurements\`: same method for Bit Rate, Bit Rate deviation, BIST duration, Hard_Reset, Timing (Max)
  and Data block timing (`other_measurements.py`; tolerance = ±5 % / ±10 % of each limit range, edit `TOLERANCE_PCT`).
  Run alone: `python other_measurements.py`.

Results and recommendation: `Tolerance_Decision.md`. Overall summary: `Overall.md`.
