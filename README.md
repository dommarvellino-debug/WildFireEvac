# Wildfire evacuation risk forecasting

The project will estimate whether a wildfire threatens an evacuation zone using
only information available during the fire's first five hours. The target
forecast horizons are 12, 24, 48, and 72 hours after that observation window.

The initial stack is:

- Python for the data and modeling pipeline
- pandas for tabular data preparation
- scikit-learn for preprocessing, imputation, and validation
- scikit-survival for Kaplan-Meier estimates and time-to-event models

Survival analysis can account for fires whose follow-up ends before a threat
event is observed (right censoring). The event definition, censoring rules, and
whether horizons are measured from ignition or the end of the five-hour window
must be established before training.

## Set up on Windows

Use Python 3.10 or newer:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

If scikit-survival has no compatible wheel for the local Windows/Python
combination, use a supported Linux environment such as WSL and install from
this same project manifest there.

## Kaplan–Meier baseline

The training CSV must contain `time_to_hit_hours` and `event` (1 = observed
hit, 0 = right-censored). A censored fire contributes to the risk set only
through its recorded follow-up time; it is not treated as a hit afterward.

```powershell
python scripts\plot_kaplan_meier.py C:\path\to\train.csv
```

The script reports hit and censored counts, estimates cumulative hit
probability as `1 - S(t)` where follow-up supports the horizon, and saves the
curve to `artifacts\kaplan_meier_train.png` by default.

## Fixed-horizon labels

Build labels from the training set without changing its source file:

```powershell
python scripts\build_horizon_labels.py C:\path\to\train.csv
```

For each horizon, a hit at or before the horizon is labeled `1`. A fire with
follow-up through the horizon and no hit by then is labeled `0`. A fire
censored before the horizon is left unknown (blank in the output CSV) and
excluded from that horizon's binary evaluation. A censor time exactly at the
horizon is known safe through that horizon and is labeled `0`.

The derived CSV includes `threat_by_12h`, `threat_by_24h`, `threat_by_48h`,
and `threat_by_72h`; the script prints positive, negative, and unknown counts
for each. By default it writes to `artifacts\train_horizon_labels.csv`.

## Competition metric (before modeling)

The evaluation implementation is in `src\wildfire_evacuation_risk\metrics.py`.
It computes:

```text
WBrier = 0.3 * Brier@24h + 0.4 * Brier@48h + 0.3 * Brier@72h
Score  = 0.3 * C-index + 0.7 * (1 - WBrier)
```

Each horizon's Brier score uses only rows with a known label at that horizon;
rows censored earlier are excluded. The C-index uses `prob_72h` as its risk
score (larger probability means higher predicted risk / earlier event), per
the current competition-scoring assumption. The public [WiDS Global Datathon
2026 Kaggle page](https://www.kaggle.com/competitions/WiDSWorldWide_GlobalDathon26)
identifies the competition but its scoring details are rendered dynamically
and were not available in the fetched page. Confirm this assumption against
the competition's scorer before treating local scores as official.

The metric expects outcomes with `time_to_hit_hours` and `event`, and aligned
prediction columns `prob_24h`, `prob_48h`, and `prob_72h`. It also reports each
horizon's Brier score and count of rows included.

## Kaplan-Meier no-feature baseline

Run the constant-probability reference on the training data:

```powershell
python scripts\evaluate_km_baseline.py C:\path\to\train.csv
```

The script fits Kaplan-Meier on the supplied outcomes, assigns the same
`1 - S(t)` probability to every row at each supported horizon, and reports
the horizon Brier scores and tied-risk C-index. These are in-sample reference
values, not cross-validated model results. A horizon beyond maximum observed
follow-up is reported as unsupported and written as blank in the predictions
CSV rather than extrapolated. With the current training data, 72h is
unsupported (maximum follow-up 66.99h), so the complete weighted Brier and
competition score cannot yet be calculated from this KM baseline.
