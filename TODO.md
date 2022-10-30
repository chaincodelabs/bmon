- [ ] alert on low peer count
- [ ] alert on high mean ping time
- [ ] "catch all" sentry logging for lines that look interesting: "warning", "invalid",
  etc.

For BG:

- [ ] save parsed coinbase on block reception
- [ ] better block propagation measures
    - chart across heights
    - average for each node
- [ ] parse block coinbase: which pool orphaned which?
  - orphans because of topology? i.e. are sub blocks in reorg beating difficult?

- [ ] compare tip with e.g. mempool.space and alert if not current
- [ ] set up sentry
- [ ] metric: at any given time, what feerate necessary to get into n blocks
- [ ] separate infrastructure in different unit file
