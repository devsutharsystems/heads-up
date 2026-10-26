# Main evaluation report

Applied mechanically from `eval/MAIN_EVAL_PLAN.md`. Registration checked before this report: 13 files match the plan's sha256 values (label sheet checked with its label column blanked), and the labelled sheet and plan are locked by `eval/main_run_manifest.json`.

100 synthetic events over 10 simulated days. Tune: 3 days, 30 events (16 labelled 1). Test: 7 days, 70 events (24 labelled 1).

## Selection (tune days only)

Rule: highest tune F1 among (format, threshold) pairs where no tune day has more than 5 alerts; ties go to the higher threshold, then to the lower format number. F1 = 2TP/(2TP+FP+FN), 0 with no true positives. An event is an alert if its score is at or above the threshold; a missing score is never an alert.

### Decider F1

| threshold | qualifies | max alerts/tune day | TP | FP | FN | TN | precision | recall | F1 | chosen |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.05 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.10 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.15 | no | 10 | 16 | 13 | 0 | 1 | 0.552 | 1.000 | 0.711 |  |
| 0.20 | no | 10 | 15 | 12 | 1 | 2 | 0.556 | 0.938 | 0.698 |  |
| 0.25 | no | 9 | 14 | 9 | 2 | 5 | 0.609 | 0.875 | 0.718 |  |
| 0.30 | no | 7 | 14 | 7 | 2 | 7 | 0.667 | 0.875 | 0.757 |  |
| 0.35 | no | 7 | 14 | 5 | 2 | 9 | 0.737 | 0.875 | 0.800 |  |
| 0.40 | no | 7 | 14 | 5 | 2 | 9 | 0.737 | 0.875 | 0.800 |  |
| 0.45 | no | 7 | 14 | 4 | 2 | 10 | 0.778 | 0.875 | 0.824 |  |
| 0.50 | no | 6 | 11 | 2 | 5 | 12 | 0.846 | 0.688 | 0.759 |  |
| 0.55 | yes | 4 | 8 | 1 | 8 | 13 | 0.889 | 0.500 | 0.640 |  |
| 0.60 | yes | 3 | 4 | 1 | 12 | 13 | 0.800 | 0.250 | 0.381 |  |
| 0.65 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.70 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.75 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.80 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.85 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.90 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.95 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |

### Decider F2

| threshold | qualifies | max alerts/tune day | TP | FP | FN | TN | precision | recall | F1 | chosen |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.05 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.10 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.15 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.20 | no | 10 | 16 | 13 | 0 | 1 | 0.552 | 1.000 | 0.711 |  |
| 0.25 | no | 10 | 14 | 12 | 2 | 2 | 0.538 | 0.875 | 0.667 |  |
| 0.30 | no | 7 | 14 | 7 | 2 | 7 | 0.667 | 0.875 | 0.757 |  |
| 0.35 | no | 6 | 13 | 3 | 3 | 11 | 0.812 | 0.812 | 0.812 |  |
| 0.40 | yes | 5 | 12 | 1 | 4 | 13 | 0.923 | 0.750 | 0.828 | **<- chosen** |
| 0.45 | yes | 4 | 10 | 1 | 6 | 13 | 0.909 | 0.625 | 0.741 |  |
| 0.50 | yes | 4 | 9 | 1 | 7 | 13 | 0.900 | 0.562 | 0.692 |  |
| 0.55 | yes | 2 | 4 | 0 | 12 | 14 | 1.000 | 0.250 | 0.400 |  |
| 0.60 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.65 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.70 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.75 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.80 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.85 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.90 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.95 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |

### Decider F3

| threshold | qualifies | max alerts/tune day | TP | FP | FN | TN | precision | recall | F1 | chosen |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.05 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.10 | no | 10 | 16 | 14 | 0 | 0 | 0.533 | 1.000 | 0.696 |  |
| 0.15 | no | 10 | 15 | 11 | 1 | 3 | 0.577 | 0.938 | 0.714 |  |
| 0.20 | no | 8 | 14 | 9 | 2 | 5 | 0.609 | 0.875 | 0.718 |  |
| 0.25 | no | 8 | 14 | 8 | 2 | 6 | 0.636 | 0.875 | 0.737 |  |
| 0.30 | no | 7 | 14 | 6 | 2 | 8 | 0.700 | 0.875 | 0.778 |  |
| 0.35 | no | 7 | 14 | 6 | 2 | 8 | 0.700 | 0.875 | 0.778 |  |
| 0.40 | no | 7 | 14 | 5 | 2 | 9 | 0.737 | 0.875 | 0.800 |  |
| 0.45 | no | 7 | 14 | 3 | 2 | 11 | 0.824 | 0.875 | 0.848 |  |
| 0.50 | no | 7 | 12 | 3 | 4 | 11 | 0.800 | 0.750 | 0.774 |  |
| 0.55 | no | 6 | 11 | 2 | 5 | 12 | 0.846 | 0.688 | 0.759 |  |
| 0.60 | yes | 4 | 10 | 0 | 6 | 14 | 1.000 | 0.625 | 0.769 |  |
| 0.65 | yes | 4 | 8 | 0 | 8 | 14 | 1.000 | 0.500 | 0.667 |  |
| 0.70 | yes | 3 | 7 | 0 | 9 | 14 | 1.000 | 0.438 | 0.609 |  |
| 0.75 | yes | 3 | 7 | 0 | 9 | 14 | 1.000 | 0.438 | 0.609 |  |
| 0.80 | yes | 2 | 5 | 0 | 11 | 14 | 1.000 | 0.312 | 0.476 |  |
| 0.85 | yes | 1 | 1 | 0 | 15 | 14 | 1.000 | 0.062 | 0.118 |  |
| 0.90 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |
| 0.95 | yes | 0 | 0 | 0 | 16 | 14 | n/a | 0.000 | 0.000 |  |

**Chosen: Decider F2 at threshold 0.40** (tune F1 0.828). Recorded in `eval/main_selection.json`.

## Test days

Not run yet. Commit `eval/main_selection.json`, then run `--local` again (the chosen format on the test events), then `--haiku`, then `--report`.
