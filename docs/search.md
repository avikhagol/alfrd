# Full-text search

Studio: Ctrl+K, then type `?` and the words (or press Tab), or the palette's
**Search file contents…**. Searches the files the Studio reads (declared logs,
result CSVs, `alfrd.yaml`, `avica.meta` files, input templates) and
`alfrd.notes.jsonl`; never measurement sets, FITS or images. All words must
match (in the same block of ~40 lines); each word of 3+ characters is a prefix.
Target names (`J0742+103`), `wd_1` and `casa.log` are single words.

## How it works

- **A: SQLite FTS5 index** per project at `$ALFRD_HOME/search/` (default
  `~/.alfrd/search/`, local disk, never next to the data). Built in the
  background on the first search (the files are scanned directly meanwhile),
  then kept current from the live watcher: changed files are re-indexed,
  removed ones dropped, growing logs indexed from where they were left.
- The index is *contentless* (text stays in the files; snippets are read from
  them) on SQLite 3.43+. On older SQLite the chunks keep their text, so the
  index is about as large as the text itself.
- **B: bounded scan** (stops after 50 hits or a few seconds) when the Python
  `sqlite3` has no FTS5.

## Benchmark (go / no-go, 2026-09-29)

Targets: query < 100 ms, index well below the text size. Measured with
`alfrd.search.SearchIndex` on text from `vasco_0.3` (casa logs, avica logs,
mpi_and_err, fringes_overview, alfrd.yaml), SQLite 3.45:

| Corpus | Text | Files | Build | Index | Query median / p95 / max | Append + re-index |
|---|---|---|---|---|---|---|
| vasco_0.3 today (staged files) | 6.1 MB | 17 | 0.1 s | 0.9 MB (15 %) | 3.0 / 4.0 / 7.8 ms | 3 ms |
| vasco_0.3 volume on 2026-09-26 (scan.json: 569 files, 116 MB of logs) | 116 MB | 238 | 1.9 s | 16.9 MB (15 %) | 12.7 / 25.5 / 33.6 ms | 3 ms |

50 queries × 3 (target names, step names, CASA task names, random words).
Variants tried on the 116 MB corpus: FTS5 storing the text with prefix indexes
2/3/4 = 182 % of the text, storing the text without prefix indexes = 139 %,
contentless without prefix indexes = 28 %, contentless with `detail=column` =
13–15 % (chosen; 1-letter prefix queries are the slowest at ~40 ms, so words
under 3 characters are matched whole). **Go: shipped as A, with B as the
fallback.**
