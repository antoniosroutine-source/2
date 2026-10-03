# NY-open 5-minute candle vs 12 EMA: results (NYOPEN-PREREG.md, commit 21c0e9f)

Data: Dukascopy NAS100 CFD 1-minute bars 2018-01..2026-09. Script: nyopen.py.

| version | trades | win | exp R | t | net $ |
|---|---|---|---|---|---|
| PRIMARY (12 EMA trail) | 2193 | 31% | +0.041 | 1.33 | +46,503 |
| A1 prior-bar trail | 2193 | 36% | +0.028 | 1.00 | +30,071 |
| A2 no trail | 2193 | 28% | +0.070 | 1.72 | +72,587 |
| primary 2018-01..2021-03 | 814 | 31% | +0.045 | 0.91 | +20,233 |
| primary 2021-04..2024-08 | 860 | 30% | +0.016 | 0.30 | +5,224 |
| primary 2024-09..2026-09 | 519 | 34% | +0.076 | 1.33 | +21,047 |
| primary 2018 | 250 | 30% | +0.015 | 0.17 | +4,303 |
| primary 2019 | 252 | 35% | +0.182 | 1.68 | +20,089 |
| primary 2020 | 251 | 31% | -0.042 | -0.54 | -3,690 |
| primary 2021 | 252 | 31% | +0.005 | 0.07 | +3,553 |
| primary 2022 | 251 | 31% | +0.098 | 1.13 | +14,277 |
| primary 2023 | 250 | 30% | +0.005 | 0.04 | -5,562 |
| primary 2024 | 252 | 30% | +0.084 | 0.93 | +10,251 |
| primary 2025 | 250 | 34% | +0.049 | 0.62 | +7,033 |
| primary 2026 | 185 | 31% | -0.053 | -0.60 | -3,751 |
| primary longs | 1148 | 32% | +0.014 | 0.38 | +21,000 |
| primary shorts | 1045 | 30% | +0.070 | 1.39 | +25,503 |
exits: {'stop': 1983, 'cap': 210, 'time': 0} median stop pts 31.2 median size 7
| K5 EMA 9 | 2193 | 33% | +0.046 | 1.57 | +50,657 |
| K5 EMA 21 | 2192 | 28% | +0.028 | 0.83 | +39,112 |
| K5 second candle 09:35-09:39 | 2193 | 27% | +0.012 | 0.31 | +10,893 |
K5 average: +0.029R
K6 one-sided p 0.0924 x3 = 0.2772

max drawdown $14,656; longest losing streak 17; Combine pass (start every day) 37%
K4 random-direction placebo (1,000 runs): mean -0.107R, 95th -0.056R; real +0.041R at percentile 100.0

| criterion | result | status |
|---|---|---|
| K1 pooled expectancy > +0.10R | +0.041R (2,193 trades) | FAIL |
| K2 futures data | none | UNRESOLVED |
| K3 every segment > 0 and pooled t >= 2.0 | segments +0.045 / +0.016 / +0.076, t 1.33 | FAIL |
| K4 random-direction placebo (1,000 runs) | 100th percentile (placebo mean -0.107R) | PASS |
| K5 EMA 9 / EMA 21 / second candle average > 0 | +0.029R | PASS |
| K6 Bonferroni over 3 versions | 0.277 | FAIL |

Verdict: NO EDGE (K1, K3, K6). The EMA direction beats a random direction by about 0.15R per trade,
but net of costs the edge is about +0.04R with a $14,656 drawdown and a 17-trade losing streak.
