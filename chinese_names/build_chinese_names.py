from __future__ import annotations

import csv
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "three_letter_names" / "research" / "broad" / "raw" / "michael_firstnames.csv"
OUT_DIR = ROOT / "chinese_names"
NAME_RE = re.compile(r"[A-Za-z]{2,5}\Z")


def compact_source_name(source_name: str) -> str | None:
    name = source_name.replace("+", "").title()
    return name if NAME_RE.fullmatch(name) else None


def read_ranked_rows() -> list[dict[str, object]]:
    best_by_name: dict[str, dict[str, object]] = {}
    with SOURCE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if not row.get("China"):
                continue
            name = compact_source_name(row["name"])
            if not name:
                continue
            china_frequency_code = int(row["China"])
            candidate = {
                "rank": 0,
                "name": name,
                "lowercase": name.lower(),
                "source_name": row["name"],
                "gender": row["gender"],
                "china_frequency_code": china_frequency_code,
                "source": "Matthias Winkelmann / Jorg Michael firstname-database; China column",
            }
            old = best_by_name.get(name)
            if old is None or china_frequency_code > int(old["china_frequency_code"]):
                best_by_name[name] = candidate

    rows = list(best_by_name.values())
    rows.sort(
        key=lambda item: (
            -int(item["china_frequency_code"]),
            len(str(item["name"])),
            str(item["name"]),
        )
    )
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def write_outputs(rows: list[dict[str, object]]) -> None:
    if len(rows) < 1000:
        raise ValueError(f"Only {len(rows)} source-backed Chinese names were found.")

    OUT_DIR.mkdir(exist_ok=True)
    selected = rows[:1000]
    names = [str(row["name"]) for row in selected]
    lower = [str(row["lowercase"]) for row in selected]

    (ROOT / "chinese_names_1000_max5.txt").write_text(
        "\n".join(names) + "\n", encoding="utf-8"
    )
    (ROOT / "chinese_names_1000_max5_lowercase.txt").write_text(
        "\n".join(lower) + "\n", encoding="utf-8"
    )

    with (OUT_DIR / "name_evidence.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)

    summary = [
        "# Chinese names, max 5 letters",
        "",
        "The root TXT file contains 1000 source-backed romanized Chinese given names.",
        "Every output name is compact A-Z only and 2-5 letters long.",
        "Names are taken from the China column of the Matthias Winkelmann / Jorg Michael firstname-database.",
        "The source uses `+` inside Chinese and Korean names to represent hyphen, space, or no separator; the TXT uses the compact no-separator form.",
        "Names are ranked by the source's China frequency code, higher first, then by shorter spelling and alphabetic order.",
        "",
        "## Output files",
        "",
        "- `../chinese_names_1000_max5.txt`: Title Case list.",
        "- `../chinese_names_1000_max5_lowercase.txt`: lowercase copy for URL or slug use.",
        "- `name_evidence.csv`: rank, compact name, source spelling, gender field, China frequency code, and source note.",
    ]
    (OUT_DIR / "SOURCES.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"found {len(rows)} unique source-backed Chinese names with max length 5")
    print(f"wrote {len(selected)} names")
    print(f"top name: {selected[0]['name']} (code {selected[0]['china_frequency_code']})")
    print(f"rank 1000: {selected[-1]['name']} (code {selected[-1]['china_frequency_code']})")


def main() -> None:
    write_outputs(read_ranked_rows())


if __name__ == "__main__":
    main()
