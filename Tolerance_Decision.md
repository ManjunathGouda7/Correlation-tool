# Tolerance Decision – Rise/Fall Time and Other Measurements

**Basis:**
- 11 DUTs (TID 16393–16557) and 22 C2 EPR PD / Flip / Updated runs for Rise/Fall Time.
- 3,146 values in 32 C2 EPR reports for the other measurements.
- Seed 20261123. The details are in `Comparison_Output\Benchmark_Comparison\Comparison_Report.html` and `Comparison_Output\Other_Measurements\Summary.html`.

**Status:** this is a recommendation, and the team still has to confirm it.

---

## 1. Rise / Fall Time: ±10 ns vs ±20 ns

| | ±10 ns | ±20 ns |
|---|---|---|
| PASS with the random offset (+5.10 / +15.90 ns) | 22 of 22 | 22 of 22 |
| FAIL at the worst case (−10 / −20 ns) | **0** | **5** (16502 PD, Flip, Updated; 16394 PD, Flip) |
| Chance of a FAIL for a random offset | 0% | 7–25% on 16502 and 16394 |
| Lowest margin to the 300 ns limit | 10.04 ns (16502 Flip) | – |

**Recommendation: use ±10 ns.**
- **No DUT is at risk with ±10 ns.** Even the worst case (−10 ns) leaves every run above 300 ns. The closest run is 16502 Flip, at 300.04 ns after the offset.
- **±20 ns can change the result for 16502 and 16394.** Their margins are 10–17 ns, so a ±20 ns tester difference could turn a PASS into a FAIL.
- **16502 and 16394 are borderline DUTs.** They should be watched in future correlation runs whichever tolerance is chosen.

## 2. Other measurements

The tolerance here is a percentage of each value's own limit range, compared at ±5% and ±10%. Measurements with only an upper limit are checked against that limit only.

| Measurement | Limit | Values | Lowest margin (% of range) | FAIL at worst case ±5% / ±10% | Recommendation |
|---|---|---|---|---|---|
| Bit Rate | 270–330 Kbps | 21 | 37.8% | 0 / 0 | ±10% is safe |
| Bit Rate deviation | < 0.25% | 21 | 73.6% | 0 / 0 | ±10% is safe |
| BIST pattern duration | 30–60 ms | 43 | 21.8% | 0 / 0 | ±10% is safe |
| Data block timing | 0–15 ms … 12000–15000 ms | 83 | 0.3% at the lower 0 limit only | 0 / 0 | ±10% is safe |
| Hard_Reset response | 24–65 / 27–65 ms | 45 | 1.3% | 16 / 31 | **±5% at most**, ideally an absolute tolerance of about ±1 ms |
| Timing check (Max) | many maximums (< 12 … < 1005 ms) | 2,933 | 0.3% | 108 / 138 | **Leave out of the tolerance study** |

**Notes:**
- **Hard_Reset response:** the DUTs answer close to the lower limit, at 25–28 ms against 24 or 27 ms. Most affected are TEST.PD.PROT.SRC.2#2, SNK.6#1, SRC3.2#2 and SNK.13#6, across 8 DUTs. A percentage tolerance of the 41 ms range (2–4 ms) is larger than the real margin.
- **Timing check (Max):**
  - The values that fail are timers set by the tester and sit just under their maximum by design. Examples:
    - 320 ms against a 325 ms max (TEST.PD.PROT.SNK.9#2, SNK.14#1, COMMON.PROC.BU.5)
    - 756 ms against a 760 ms max (COMMON.PROC.PD3.4)
  - These values measure the tester's own timing, not the DUT, so a correlation tolerance doesn't apply to them.
  - If they are kept, they need a tolerance per check, not one percentage for all of them.

## 3. Summary of the decision

1. **Rise/Fall Time:** use **±10 ns**, and flag 16502 and 16394 as borderline.
2. **Bit Rate, Bit Rate deviation, BIST pattern duration, Data block timing:** use **±10%** of the limit range. None of them changes result.
3. **Hard_Reset response:** use an absolute tolerance of **±1 ms**, or ±5% at most. This needs team agreement.
4. **Tester-driven timing checks (Max = … ms):** **leave them out** of the tolerance comparison.

**To change the inputs and rerun:**
- The percentage tolerances are set in `TOLERANCE_PCT` in `other_measurements.py`.
- Run `python tolerance_compare.py` again; it regenerates everything.
