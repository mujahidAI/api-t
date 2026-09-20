# Chinese names, max 5 letters

The root TXT file contains 1000 source-backed romanized Chinese given names.
Every output name is compact A-Z only and 2-5 letters long.
Names are taken from the China column of the Matthias Winkelmann / Jorg Michael firstname-database.
The source uses `+` inside Chinese and Korean names to represent hyphen, space, or no separator; the TXT uses the compact no-separator form.
Names are ranked by the source's China frequency code, higher first, then by shorter spelling and alphabetic order.

## Output files

- `../chinese_names_1000_max5.txt`: Title Case list.
- `../chinese_names_1000_max5_lowercase.txt`: lowercase copy for URL or slug use.
- `name_evidence.csv`: rank, compact name, source spelling, gender field, China frequency code, and source note.
