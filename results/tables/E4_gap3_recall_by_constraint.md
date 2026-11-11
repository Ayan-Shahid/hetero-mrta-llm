**Model-extraction recall by fact type (1.00 = every fact of that type recovered)**

| Model      | Method                                             | Brief    |    F1 |   task_req |   task_payload |   task_duration |   task_deadline |   precedence |   standby |   robot_cap |   horizon |
|:-----------|:---------------------------------------------------|:---------|------:|-----------:|---------------:|----------------:|----------------:|-------------:|----------:|------------:|----------:|
| qwen2.5-7b | LLM-formulate                                      | explicit | 0.956 |       0.96 |           1    |            1    |            0.95 |         0    |         1 |        0.98 |      1    |
| qwen2.5-7b | LLM-formulate                                      | informal | 0.729 |       0.82 |           0.96 |            0.78 |            0.5  |         0.07 |         1 |        0.78 |      0.14 |
| qwen2.5-7b | Agent (full)                                       | explicit | 0.953 |       0.97 |           0.99 |            0.99 |            0.74 |         0.47 |         1 |        0.99 |      1    |
| qwen2.5-7b | Agent (full)                                       | informal | 0.838 |       0.74 |           0.91 |            0.92 |            0.68 |         0.36 |         1 |        0.79 |      0.88 |
| qwen2.5-7b | Agent without G3 (single-pass model, no questions) | explicit | 0.957 |       0.97 |           1    |            0.99 |            0.81 |         0.36 |         1 |        0.99 |      1    |
| qwen2.5-7b | Agent without G3 (single-pass model, no questions) | informal | 0.775 |       0.91 |           0.98 |            0.98 |            0.55 |         0.05 |         1 |        0.8  |      1    |
| qwen2.5-7b | Agent without G2 (no verification)                 | explicit | 0.953 |       0.98 |           0.98 |            0.99 |            0.77 |         0.5  |         1 |        0.98 |      1    |
| qwen2.5-7b | Agent without G2 (no verification)                 | informal | 0.845 |       0.74 |           0.91 |            0.87 |            0.74 |         0.46 |         1 |        0.82 |      0.76 |
