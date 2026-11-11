**Gap 3: explicit vs informal briefs (paired by scenario and repeat)**

| Model      | Method                                             |   Feasible % (explicit) | Model F1 (explicit)   |   Feasible % (informal) | Model F1 (informal)   |   Drop (pp) |   McNemar p |
|:-----------|:---------------------------------------------------|------------------------:|:----------------------|------------------------:|:----------------------|------------:|------------:|
| qwen2.5-7b | LLM-direct                                         |                     0   | -                     |                     2.5 | -                     |        -2.5 |    1        |
| qwen2.5-7b | LLM-formulate                                      |                     5   | 0.956                 |                     0   | 0.729                 |         5   |    0.5      |
| qwen2.5-7b | COHERENT-style PEFA                                |                     2.5 | -                     |                     5   | -                     |        -2.5 |    1        |
| qwen2.5-7b | Agent (full)                                       |                    52.5 | 0.953                 |                    12.5 | 0.838                 |        40   |    0.000402 |
| qwen2.5-7b | Agent without G1 (no solvers)                      |                     0   | 0.953                 |                     5   | 0.848                 |        -5   |    0.5      |
| qwen2.5-7b | Agent without G2 (no verification)                 |                    70   | 0.953                 |                    25   | 0.845                 |        45   |    0.000277 |
| qwen2.5-7b | Agent without G3 (single-pass model, no questions) |                    35   | 0.957                 |                    15   | 0.775                 |        20   |    0.0768   |
