Backtest on 8,448 listing-days (24 mock listings, seed 42; first 14 days of each listing excluded as warm-up).

| Strategy | Days flagged | Precision | Genuine sales caught | Fake deals flagged | False alarms / listing / month |
|---|---:|---:|---:|---:|---:|
| Naive: 10%+ below 30-day avg | 1,454 | 34% | 142/143 (99%) | 61/71 | 3.41 |
| Naive: 10%+ drop vs. yesterday | 213 | 67% | 143/143 (100%) | 70/71 | 0.25 |
| Naive: at 90-day low | 231 | 70% | 80/143 (56%) | 11/71 | 0.25 |
| Deal rule (this project) | 327 | 83% | 89/143 (62%) | 3/71 | 0.20 |
