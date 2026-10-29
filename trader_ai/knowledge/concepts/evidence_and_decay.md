# Why "proven" strategies usually disappoint

Most strategies described as "proven" online are in-sample backtests. The academic record is more sobering:

- **Data snooping.** Test enough rules on the same history and some look great by chance. Sullivan, Timmermann & White (1999, *Journal of Finance*) re-tested technical trading rules with a bootstrap that accounts for the search. Most of the apparent edge disappeared.
- **Multiple testing.** Harvey, Liu & Zhu (2016, *Review of Financial Studies*) argue a new factor should clear a t-statistic of about 3, not 2, because hundreds have been tried.
- **Decay after publication.** McLean & Pontiff (2016, *Journal of Finance*) found that predictability in 97 published anomalies fell substantially after publication. Part of the effect was a statistical fluke, and part was arbitraged away.
- **Volatility management, as an example.** Moreira & Muir (2017, *Journal of Finance*) reported that scaling exposure by inverse recent volatility raised Sharpe ratios. Cederburg, O'Doherty, Wang & Yan (2020, *Journal of Financial Economics*) found that most of those gains did not survive realistic out-of-sample implementation.

What this project does about it: every strategy goes through walk-forward testing, 2x cost stress, a comparison with buy-and-hold and an untouched holdout. Every trial is counted in the experiment registry, and the promotion gate rejects anything whose confidence interval includes zero. A strategy with a famous paper behind it gets no exemption.
