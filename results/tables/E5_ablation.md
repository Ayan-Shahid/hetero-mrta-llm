**E5: removing one gap component at a time (same runs, paired)**

| Model      | Configuration                                      |   Runs |   Feasible % |   Success@5 % |   Mean gap % |   Model F1 |   Tokens/run |   LLM calls/run | Δ feasible vs full (pp)   | McNemar p   |
|:-----------|:---------------------------------------------------|-------:|-------------:|--------------:|-------------:|-----------:|-------------:|----------------:|:--------------------------|:------------|
| qwen2.5-7b | Agent (full)                                       |     80 |         32.5 |          26.2 |          5.1 |      0.9   |        60798 |            14.1 | -                         | -           |
| qwen2.5-7b | Agent without G1 (no solvers)                      |     80 |          2.5 |           2.5 |          1.2 |      0.908 |        55710 |            13.2 | -30.0                     | 8.05e-07    |
| qwen2.5-7b | Agent without G2 (no verification)                 |     80 |         47.5 |          38.8 |          4.2 |      0.903 |        35793 |             9.9 | 15.0                      | 0.0227      |
| qwen2.5-7b | Agent without G3 (single-pass model, no questions) |     80 |         25   |          16.2 |          7.1 |      0.881 |        53122 |            10.9 | -7.5                      | 0.286       |
