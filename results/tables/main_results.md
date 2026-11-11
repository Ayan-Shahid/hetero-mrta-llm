**Main results on the test split (mean ± std over repeats)**

| Model     | Method             | Tier   |   Runs |   Repeats |   Feasible % |   Success@5 % |   Mean gap % |   Median gap % |   Plan time s |   LLM calls |
|:----------|:-------------------|:-------|-------:|----------:|-------------:|--------------:|-------------:|---------------:|--------------:|------------:|
| classical | MILP (exact)       | S      |     16 |         1 |        100   |         100   |          0   |            0   |           0   |           0 |
| classical | MILP (exact)       | M      |     16 |         1 |        100   |         100   |          0   |            0   |           0.6 |           0 |
| classical | MILP (exact)       | L      |      8 |         1 |        100   |         100   |          0   |            0   |          27.7 |           0 |
| classical | MILP (exact)       | all    |     40 |         1 |        100   |         100   |          0   |            0   |           0.3 |           0 |
| classical | Iterated Hungarian | S      |     16 |         1 |         87.5 |          75   |          8   |            0   |           0   |           0 |
| classical | Iterated Hungarian | M      |     16 |         1 |         62.5 |           6.2 |         11.4 |           10   |           0   |           0 |
| classical | Iterated Hungarian | L      |      8 |         1 |         75   |           0   |         39.5 |           35.9 |           0   |           0 |
| classical | Iterated Hungarian | all    |     40 |         1 |         75   |          32.5 |         15.5 |            8   |           0   |           0 |
| classical | SSI auction        | S      |     16 |         1 |         93.8 |          87.5 |          1.3 |            0   |           0   |           0 |
| classical | SSI auction        | M      |     16 |         1 |         87.5 |          12.5 |         17.3 |           13.3 |           0   |           0 |
| classical | SSI auction        | L      |      8 |         1 |        100   |          12.5 |         35.2 |           27.9 |           0   |           0 |
| classical | SSI auction        | all    |     40 |         1 |         92.5 |          42.5 |         14.7 |            8.5 |           0   |           0 |
