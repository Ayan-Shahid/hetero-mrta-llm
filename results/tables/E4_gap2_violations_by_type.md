**Gap 2: ground-truth violations by type (total counts)**

| Model      | Method                                             |   Runs |   Infeasible % |   capability |   payload |   unreachable |   deadline |   endurance |   standby |   deadlock |   missing |
|:-----------|:---------------------------------------------------|-------:|---------------:|-------------:|----------:|--------------:|-----------:|------------:|----------:|-----------:|----------:|
| qwen2.5-7b | LLM-direct                                         |     80 |           98.8 |          116 |        71 |            36 |         39 |           0 |         2 |          4 |        99 |
| qwen2.5-7b | LLM-formulate                                      |     80 |           97.5 |            2 |         0 |             0 |          0 |           0 |         0 |          2 |         0 |
| qwen2.5-7b | COHERENT-style PEFA                                |     80 |           96.2 |          127 |        71 |            37 |         39 |           0 |         9 |          8 |        38 |
| qwen2.5-7b | Agent (full)                                       |     80 |           67.5 |           11 |         6 |             1 |          4 |           0 |         0 |          4 |         2 |
| qwen2.5-7b | Agent without G1 (no solvers)                      |     80 |           97.5 |           80 |        56 |            32 |         26 |           1 |        10 |          3 |        24 |
| qwen2.5-7b | Agent without G2 (no verification)                 |     80 |           52.5 |           23 |         9 |             4 |          4 |           0 |         0 |          4 |         3 |
| qwen2.5-7b | Agent without G3 (single-pass model, no questions) |     80 |           75   |            8 |         7 |             2 |         13 |           1 |         0 |          2 |         1 |
