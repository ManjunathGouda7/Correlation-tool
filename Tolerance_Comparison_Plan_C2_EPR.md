# Tolerance Comparison – Plan and Status (C2 EPR)

This file maps each step of `Tolerance_Based_Measurement_Comparison_Implementation_Plan.md` to how it is done. The results and the recommendation are in `Tolerance_Decision.md`, and the overall summary is in `Overall.md`.

## Status per plan step

| # | Plan step | Status | How it is done |
|---|---|---|---|
| 1 | Extract the six Rise/Fall values | Done | `BMC_PHY_TX_EYE_5` block in each C2 EPR `GRL_USB_PD_Report_Run_*.csv`: 22 runs, 11 DUTs. The nested duplicate `TID 16557\TID 16502` is skipped |
| 2 | Reference values | Done | Average, minimum and maximum of each value over all runs, in `Baseline\reference_values.csv` |
| 3 | Random offset per method | Done | ±10 ns and ±20 ns, drawn once with a fixed seed (default 20261123) and saved in `config.json`. `--offset10` / `--offset20` force a value |
| 4 | Apply the offset | Done | The same offset goes on all six values of every DUT and test case. The originals are kept in their own columns |
| 5–6 | Reports per method | Done | `Tolerance_10ns\` and `Tolerance_20ns\`, with copies of the real GRL reports holding the adjusted values (`grl_report_writer.py`) |
| 7 | Output folders | Done | One folder per TID with `GRL_Reports\`, `Reports\`, `Plots\` and `Summary\`, plus `Overall_Summary` per method |
| 8 | Benchmark comparison | Done | Runs are matched on TID and test case and given a change status. Unmatched runs get their own sheet |
| 9 | Benchmark table | Done | `Comparison_Table.xlsx/.csv` and `Comparison_Summary.csv`: totals, pass %, changed runs, worst case, chance of FAIL |
| 10 | Graphs | Done | 6 graphs in `Benchmark_Comparison\Graphs\` |
| 11 | Other measurements | Done | `other_measurements.py`: Bit Rate, Bit Rate deviation, BIST duration, Hard_Reset, Timing (Max), Data block timing, each with its own limit (tolerance = ±5% / ±10% of the limit range). Output in `Other_Measurements\` |
| 12 | Final analysis and decision | Done (team to confirm) | `Tolerance_Decision.md` and `Comparison_Report.html` |
| 13 | Validation | Done | See the checks below |

## Choices made

- **Duty cycle:** this data has none. A test case is the run type (PD, PD_Flip, PD_Updated, PD_port0).
- **Pass rule (Rise/Fall):** a run passes when the adjusted Rise Min and Fall Min are at least 300 ns. The report only gives a minimum limit.
- **Other measurements:**
  - The tolerance is a percentage of each value's own limit range, so ns values are never applied to other units.
  - Upper-limit-only checks use only that limit.
  - Adjusted times and percentages are clamped at 0.
- **One random draw isn't enough to decide,** so a worst-case result and a chance-of-FAIL figure are added for every value.

## Validation done

- The adjusted values in the GRL copies equal the original plus the offset in all five files of a run, for example 317.075027 + 5.096938 = 322.171965. The C2 sources are unchanged.
- Forced worst case (−10 / −20 ns):
  - The expected 5 runs fail.
  - In 16394 PD, the counts change from 64/0 to 63/1 at 98% in the HTML, both CSVs and the XML.
  - 16502 PD's PHY.ALL.2, which was already FAIL, isn't counted twice.
- Re-running with the same seed gives the same offsets and results.

## How to run

```
python tolerance_compare.py                                # everything, random offsets
python tolerance_compare.py --offset10 -10 --offset20 -20  # worst case
python other_measurements.py                               # other measurements only
```
