# Source audit, 9 October 2026

The October 2 brief points to several unrelated arXiv records and one unavailable repository. Matching title/method primary sources were verified before implementing adapters.

| Item | Brief identifier | Matching study |
|---|---|---|
| MemAgent | 2610.00070 | [2609.32521](https://arxiv.org/abs/2609.32521) |
| Selection versus extraction | 2610.00650 | [2609.34227](https://arxiv.org/abs/2609.34227) |
| You're Hired | 2610.01134 | [2609.38816](https://arxiv.org/abs/2609.38816) |
| MADBench | 2610.01223 | [2609.39146](https://arxiv.org/abs/2609.39146) |
| Held-out skill generalization | 2610.01319 | [2609.39148](https://arxiv.org/abs/2609.39148) |
| ReCAP | 2610.01179 | [2609.40118](https://arxiv.org/abs/2609.40118) |
| Rep2Skill watchlist | 2610.00915 | [2609.39149](https://arxiv.org/abs/2609.39149) |

The matching Engram implementation is [ris3abh/Engram](https://github.com/ris3abh/Engram), not the unavailable `Edward-Sun-Official/Engram` URL. Its license and revision are pinned in the adapter NOTICE.

Availability also changed from the briefs: HGP is reachable at [Ouan6/HGP-](https://github.com/Ouan6/HGP-) with an MIT license. [Learn2Play-Bench](https://github.com/liushiliushi/Learn2Play-Bench) has game code but no explicit software license in the reviewed snapshot. Mem++ and MemAgent have licensed public code; selected pure/interface components are imported. ReCAP remains empty. SkillAA still lacks an explicit software license. Zenodo consensus data could not be retrieved; RH-Detect's dataset card/download returned 401 and its asserted aggregate licensing/row count was not independently established. No unverifiable dataset claims are used as integration prerequisites or empirical results.
