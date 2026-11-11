**Test split: feasible plans by tier, success@5 and gap (mean ± std over repeats)**

| Model     | Method             | Feasible S   | Feasible M   | Feasible L   |   Feasible (all) |   Success@5 (all) | Mean gap   |   Tokens/run |   Repeats |
|:----------|:-------------------|:-------------|:-------------|:-------------|-----------------:|------------------:|:-----------|-------------:|----------:|
| classical | MILP (exact)       | 100%         | 100%         | 100%         |            100   |             100   | 0.0%       |            0 |         1 |
| classical | Iterated Hungarian | 88%          | 62%          | 75%          |             75   |              32.5 | 15.5%      |            0 |         1 |
| classical | SSI auction        | 94%          | 88%          | 100%         |             92.5 |              42.5 | 14.7%      |            0 |         1 |
