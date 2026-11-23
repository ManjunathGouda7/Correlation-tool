# Corelation – Overall Summary

There are two parts:
1. **VD PWR report update:** the PHY test results in the VD PWR EPR reports are fixed using the C2 EPR results.
2. **Tolerance comparison:** ±10 ns and ±20 ns are compared for Rise and Fall Time, to decide which tolerance to use.

---

## 1. Input data

| Folder | Content |
|---|---|
| `C2_EPR\` | C2 EPR runs for 11 DUTs (TID 16393, 16394, 16396, 16397, 16413, 16502, 16503, 16506, 16554, 16556, 16557): PD, PD_Flip, PD_Updated and port0 runs |
| `VDPWR_EPR\` | Original VD PWR EPR runs for the same 11 DUTs. This folder is never changed |
| `Output\VDPWR_EPR\` | Updated copy of the VD PWR runs (part 2) |
| `Comparison_Output\` | Tolerance comparison output (part 3) |

Notes:
- `C2_EPR\TID 16557\TID 16502\` is a duplicate copy of the 16502 runs, and both tools ignore it.
- The 16393 C2 PD run was added later (`C2_EPR\TID 16393\16393_PD_2026_11_02-10_57_29`).

---

## 2. VD PWR report update (`corelation.py`, `corelation_ui.py`)

**Problem:** in the VD PWR runs, TEST.PD.PHY.ALL.1–3 were INCOMPLETE or NOT_EXECUTED. The C2 EPR tester ran the same tests on the same DUTs and they passed.

**Steps**, run on a copy in `Output\VDPWR_EPR\`:
1. Delete `testresult.deb` from the PHY.ALL.1–5 `.grltrace` files.
2. Delete the PDF and CSV report files.
3. Copy the C2 EPR results for PHY.ALL.1–3 into `PD_Merged.xml`, keeping the VD PWR packet numbers.
4. Update the HTML reports (per-test HTML, PD_Merged.html, GRL_USB_PD_Report, Summary_Run), including the PASS/INCOMPLETE counts and PASS Rate.
5. Delete `PDMerged\AMS_Run_*.html`.

**Running many DUTs at once:** the tool now accepts the whole `C2_EPR` folder and matches each VD PWR run to the C2 run with the same TID:
- It uses the C2 **PD** run (not Flip or Updated) that has PHY.ALL.1–3.
- A test that doesn't pass there is taken from another non-Flip run where it passes. For **TID 16502**, PHY.ALL.2 comes from `16502_PD_Updated`, because the PD run fails the bit-rate check.
- Nested duplicate folders are ignored.
- A DUT with no C2 PD run gets steps 1, 2 and 5 only, and the log says so.

**Result:** all 11 DUTs now show **PHY.ALL.1–3 = PASS** in `Output\VDPWR_EPR\`.

**How to run:**
- In the UI (`python corelation_ui.py`): set the input to `D:\Corelation\VDPWR_EPR`, the C2 folder to `D:\Corelation\C2_EPR`, and the output to `D:\Corelation\Output\VDPWR_EPR`.
- From the command line: `python corelation.py "Output\VDPWR_EPR" --c2 "C2_EPR"`

---

## 3. Tolerance comparison ±10 ns vs ±20 ns (`tolerance_compare.py`, `grl_report_writer.py`)

### Data
Each C2 report has a test called `BMC_PHY_TX_EYE_5` (inside TEST.PD.PHY.ALL.2) that lists six values:
- Rise Time average, minimum and maximum
- Fall Time average, minimum and maximum

The only limit is a **minimum of 300 ns**. That gives **22 runs from 11 DUTs**, and all of them pass today.

### Method
1. **Reference:** the average of each of the six values over all runs, saved in `Baseline\reference_values.csv`.
2. **Random number:** one per method, drawn once with a fixed seed: a value within ±10 ns and a value within ±20 ns. Both are saved in `config.json`.
3. **Same number everywhere:** each method's number is added to all six values, for every DUT and every test case.
4. **Re-check:** a run passes if its adjusted Rise minimum and Fall minimum are both still at least 300 ns.
5. **Two sets of reports:** `Tolerance_10ns\` and `Tolerance_20ns\`, with one folder per TID.
6. **Benchmark:** a table comparing both methods for every run, with totals, pass % and the runs whose result changed.
7. **Graphs and a final report** to support the decision.

### Output layout
```
Comparison_Output\
├── config.json                      seed, offsets, rule, input files
├── Baseline\                        measurements.csv, reference_values.csv, vdpwr_reports.csv
├── Tolerance_10ns\
│   ├── Overall_Summary.html / .csv
│   └── TID_xxxxx\
│       ├── GRL_Reports\             copies of the real GRL reports with adjusted values
│       ├── Reports\                 TID report (.html / .csv)
│       ├── Plots\                   Rise/Fall Min, original vs adjusted, against the 300 ns limit
│       └── Summary\                 summary.html / .csv
├── Tolerance_20ns\                  (same layout)
├── Other_Measurements\              other measurements (see 3b)
└── Benchmark_Comparison\
    ├── Comparison_Table.xlsx / .csv
    ├── Comparison_Summary.csv
    ├── Comparison_Report.html       observations, recommendation, graphs, links
    └── Graphs\                      PASS/FAIL count, pass %, changed runs,
                                     DUT margin, chance of FAIL, per test case result
```

### GRL report copies
- **Files copied:** GRL_USB_PD_Report (html/csv), Summary_Run (html/csv), PD_Merged.html/.xml, TEST.PD.PHY.ALL.2.html and the eye diagram images. PDFs are left out because they can't be edited.
- **Values:** the six Rise/Fall values are replaced with the adjusted values.
- **If a run drops below 300 ns:** BMC_PHY_TX_EYE_5 and TEST.PD.PHY.ALL.2 become FAIL, and the PASS/FAIL counts and PASS Rate are updated in the HTML, CSV and XML.
- **Labelling:** each HTML has a yellow note saying the values are adjusted.

### Results (seed 20261123)

| | ±10 ns | ±20 ns |
|---|---|---|
| Random offset | +5.10 ns | +15.90 ns |
| PASS (with the random offset) | 22 of 22 | 22 of 22 |
| FAIL at the worst case (−10 / −20 ns) | 0 | 5 (16502 ×3, 16394 ×2) |
| Chance of a FAIL for a random offset | 0% | up to ~25% (16502) |

**Margin to the 300 ns limit (lowest per DUT):**

| TID | Lowest Min (ns) | Margin (ns) |
|---|---|---|
| 16502 | 310.04 | **10.04** |
| 16394 | 316.33 | **16.33** |
| 16554 | 351.68 | 51.68 |
| others | > 379 | > 79 |

### Observations
- **±10 ns can never fail a run.** The closest DUT is 16502 at 310.04 ns, so even −10 ns leaves 300.04 ns.
- **±20 ns fails runs only when the random number is negative:**
  - Below −10.04 ns, 16502 fails.
  - Below −16.33 ns, 16394 also fails.
- **One random number mostly decides the outcome.** The decision should therefore be based on the worst-case and chance-of-FAIL results, not on a single draw.
- **16502 PD and Flip show PHY.ALL.2 = FAIL in the GRL copies under both methods.** That comes from their original bit-rate check failure, not from Rise/Fall Time.

### How to run
```
python tolerance_compare.py                              # random offsets, seed 20261123
python tolerance_compare.py --seed 7                     # another random draw
python tolerance_compare.py --offset10 -10 --offset20 -20  # worst case
```

### 3b. Other measurements (`other_measurements.py`)
The same method is applied to the other measurements in the C2 reports, each against its own limit.

**How the tolerance works here:**
- The tolerance is a percentage of each value's limit range: ±5% and ±10%.
- One random number is drawn per method and measurement.
- Checks with only an upper limit use only that limit, and adjusted values are clamped at 0.

| Measurement | Limit | Values | FAIL at worst case ±5% / ±10% |
|---|---|---|---|
| Bit Rate | 270–330 Kbps | 21 | 0 / 0 |
| Bit Rate deviation | < 0.25% | 21 | 0 / 0 |
| BIST pattern duration | 30–60 ms | 43 | 0 / 0 |
| Data block timing | 0–15 … 12000–15000 ms | 83 | 0 / 0 |
| Hard_Reset response | 24/27–65 ms | 45 | 16 / 31 |
| Timing check (Max) | < 12 … < 1005 ms | 2,933 | 108 / 138 |

Output is in `Comparison_Output\Other_Measurements\`:
- `Summary.html`, `Summary.xlsx` and `Graphs\`
- one folder per measurement, with `report.html`, `values.csv`, `per_tid.csv` and `margin.png`

### Decision (see `Tolerance_Decision.md`; team to confirm)
1. **Rise/Fall Time:** use **±10 ns**. No DUT changes result even at the worst case. 16502 and 16394 are borderline.
2. **Bit Rate, Bit Rate deviation, BIST duration, Data block timing:** use **±10%** of the limit range. They are safe.
3. **Hard_Reset response:** use **±1 ms** (or ±5% at most). The DUTs answer at 25–28 ms against a 24/27 ms lower limit.
4. **Timing check (Max):** **leave it out.** These values are timers set by the tester and sit just under their maximum by design (for example 320 of 325 ms).

---

## 4. Files

| File | Purpose |
|---|---|
| `corelation.py` | VD PWR report update (steps 1–5), matching DUTs by TID |
| `corelation_ui.py` | UI for `corelation.py` |
| `tolerance_compare.py` | ±10 / ±20 ns comparison: data extraction, offsets, reports, benchmark, graphs |
| `grl_report_writer.py` | Writes the adjusted values and results into copies of the GRL reports |
| `other_measurements.py` | Same comparison for the other measurements (called by `tolerance_compare.py`) |
| `Tolerance_Decision.md` | Results and recommendation |
| `Tolerance_Based_Measurement_Comparison_Implementation_Plan.md` | Original plan |
| `Tolerance_Comparison_Plan_C2_EPR.md` | Status of each plan step |
| `Corelation.md` | Usage notes |
| `Overall.md` | This summary |

---

## 5. Open items

1. **Confirm the decision:** the team needs to sign off the recommendation in `Tolerance_Decision.md`. That includes the Hard_Reset tolerance and leaving out the tester-driven timing checks.
2. **GRL report copies:** these are made for Rise/Fall Time only. The other measurements have analysis reports, not edited GRL copies.
