# Held-out validation results: council 2 finalists

Pre-registration: VALIDATION-PREREG.md (commit 89176b4). Each finalist run once, rules frozen.
Design-data reruns reproduce the council's reported numbers exactly (n = 527 / 666 / 793 / 262).

| Finalist | Design 2021-04..2024-08 | Held-out B 2024-09..2026-09 | Verdict |
|---|---|---|---|
| trend/finalist_1 noise-area momentum | n=527, +0.461R, t=3.68 | n=281, win 18%, **-0.173R**, t=-1.40, -$23,277 | FAIL |
| quant/finalist_1 failed 15-min ORB | n=666, +0.209R, t=1.63 | n=408, win 16%, **+0.001R**, t=0.01, +$409 | FAIL |
| quant/finalist_2 30-min ORB | n=793, +0.117R, t=1.82 | n=487, win 34%, **-0.051R**, t=-0.73, -$6,569 | FAIL |
| trader/finalist_1 London sweep of Asia | n=262, +0.111R, t=1.09 | n=167, win 32%, **-0.006R**, t=-0.04, -$521 | FAIL |

Every finalist fails the pre-registered requirement (expectancy > 0 in held-out segment B, and
>= +0.10R pooled with t >= 1.5), so segment A (2018-01..2021-03) cannot change the verdict.
None is built. Per the pre-registration they are dropped, not tweaked.
