# Indian names, max 5 letters

The root TXT file contains 1000 source-backed romanized Indian given-name entries.
Every output name is compact A-Z only and 2-5 letters long.
Names from the frequency-coded India/Sri Lanka column of the Matthias Winkelmann / Jorg Michael firstname-database are ranked first.
The remaining slots are filled from `raw/Indian_Names.csv`, an open Indian-name CSV, after filtering and deduping.
Common honorific/surname-like tokens such as Smt, Shri, Kaur, Khan, Kumar, Kumari, Devi, and Begum are excluded.

## Output files

- `../indian_names_1000_max5.txt`: Title Case list.
- `../indian_names_1000_max5_lowercase.txt`: lowercase copy for URL or slug use.
- `name_evidence.csv`: rank, name, source spelling, source name, source priority, and frequency code when available.
