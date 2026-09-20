# Codeshare Fetcher

This repository contains a Python fetcher for reading public Codeshare room pages with bounded concurrency, retry/backoff, resume support, and plain-text output.

Included input lists:

- `four_letter_names_1000_part2.txt`: four-letter given-name list, ranks 1001-2000 from the local source ranking.
- `four_letter_names_1000_part2_lowercase.txt`: lowercase copy for Codeshare slugs.

Install dependencies:

```powershell
pip install -r requirements_codeshare_fetcher.txt
```

Run against the included lowercase list:

```powershell
python fetch_codeshares_common_names.py --input four_letter_names_1000_part2_lowercase.txt --output codeshare_four_letter_names_part2_results.txt --workers 2 --interval 3
```

The fetcher writes a `.txt` result file and a matching `.sqlite3` checkpoint next to it. If a run stops midway, run the same command again to resume.
