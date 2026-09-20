from __future__ import annotations

import csv
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MICHAEL_SOURCE = ROOT / "three_letter_names" / "research" / "broad" / "raw" / "michael_firstnames.csv"
INDIAN_CSV = ROOT / "indian_names" / "raw" / "Indian_Names.csv"
OUT_DIR = ROOT / "indian_names"
NAME_RE = re.compile(r"[A-Za-z]{2,5}\Z")

BLOCKED = {
    "Begum",
    "Devi",
    "Kaur",
    "Khan",
    "Kumar",
    "Kumari",
    "Moh",
    "Md",
    "Mrs",
    "Ms",
    "Mr",
    "Shri",
    "Sri",
    "Smt",
}


def clean_name(raw: str) -> str | None:
    name = re.sub(r"[^A-Za-z]", "", raw).title()
    if not NAME_RE.fullmatch(name):
        return None
    if name in BLOCKED:
        return None
    return name


def michael_rows() -> list[dict[str, object]]:
    rows = []
    with MICHAEL_SOURCE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if not row.get("India/Sri Lanka"):
                continue
            name = clean_name(row["name"])
            if not name:
                continue
            rows.append(
                {
                    "rank": 0,
                    "name": name,
                    "lowercase": name.lower(),
                    "gender": row["gender"],
                    "source_name": row["name"],
                    "source": "Matthias Winkelmann / Jorg Michael firstname-database; India/Sri Lanka column",
                    "source_priority": 1,
                    "frequency_code": int(row["India/Sri Lanka"]),
                }
            )
    rows.sort(
        key=lambda item: (
            -int(item["frequency_code"]),
            len(str(item["name"])),
            str(item["name"]),
        )
    )
    return rows


def open_csv_rows() -> list[dict[str, object]]:
    rows = []
    with INDIAN_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), 2):
            name = clean_name(row.get("Name", ""))
            if not name:
                continue
            rows.append(
                {
                    "rank": 0,
                    "name": name,
                    "lowercase": name.lower(),
                    "gender": "",
                    "source_name": row.get("Name", ""),
                    "source": "balasahebgulave/Dataset-Indian-Names Indian_Names.csv",
                    "source_priority": 2,
                    "frequency_code": "",
                    "source_row": row_number,
                }
            )
    return rows


def ranked_rows() -> list[dict[str, object]]:
    seen = set()
    rows = []
    for row in michael_rows() + open_csv_rows():
        key = str(row["name"]).lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
        row.setdefault("source_row", "")
    return rows


def write_outputs(rows: list[dict[str, object]]) -> None:
    if len(rows) < 2000:
        raise ValueError(f"Only {len(rows)} source-backed Indian names were found.")

    OUT_DIR.mkdir(exist_ok=True)
    selected = rows[:1000]
    selected_part2 = rows[1000:2000]
    names = [str(row["name"]) for row in selected]
    lower = [str(row["lowercase"]) for row in selected]
    names_part2 = [str(row["name"]) for row in selected_part2]
    lower_part2 = [str(row["lowercase"]) for row in selected_part2]

    (ROOT / "indian_names_1000_max5.txt").write_text(
        "\n".join(names) + "\n", encoding="utf-8"
    )
    (ROOT / "indian_names_1000_max5_lowercase.txt").write_text(
        "\n".join(lower) + "\n", encoding="utf-8"
    )
    (ROOT / "indian_names_1000_max5_part2.txt").write_text(
        "\n".join(names_part2) + "\n", encoding="utf-8"
    )
    (ROOT / "indian_names_1000_max5_part2_lowercase.txt").write_text(
        "\n".join(lower_part2) + "\n", encoding="utf-8"
    )

    fields = [
        "rank",
        "name",
        "lowercase",
        "gender",
        "source_name",
        "source",
        "source_priority",
        "frequency_code",
        "source_row",
    ]

    with (OUT_DIR / "name_evidence.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)

    with (OUT_DIR / "name_evidence_1001_2000.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected_part2)

    summary = [
        "# Indian names, max 5 letters",
        "",
        "The root TXT files contain two batches of 1000 source-backed romanized Indian given-name entries.",
        "Every output name is compact A-Z only and 2-5 letters long.",
        "Names from the frequency-coded India/Sri Lanka column of the Matthias Winkelmann / Jorg Michael firstname-database are ranked first.",
        "The remaining slots are filled from `raw/Indian_Names.csv`, an open Indian-name CSV, after filtering and deduping.",
        "Common honorific/surname-like tokens such as Smt, Shri, Kaur, Khan, Kumar, Kumari, Devi, and Begum are excluded.",
        "",
        "## Output files",
        "",
        "- `../indian_names_1000_max5.txt`: Title Case list.",
        "- `../indian_names_1000_max5_lowercase.txt`: lowercase copy for URL or slug use.",
        "- `../indian_names_1000_max5_part2.txt`: Title Case list for ranks 1001-2000.",
        "- `../indian_names_1000_max5_part2_lowercase.txt`: lowercase copy for ranks 1001-2000.",
        "- `name_evidence.csv`: rank, name, source spelling, source name, source priority, and frequency code when available for ranks 1-1000.",
        "- `name_evidence_1001_2000.csv`: evidence for ranks 1001-2000.",
    ]
    (OUT_DIR / "SOURCES.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"found {len(rows)} unique source-backed Indian names with max length 5")
    print(f"wrote {len(selected)} names to part 1")
    print(f"wrote {len(selected_part2)} names to part 2")
    print(f"top name: {selected[0]['name']}")
    print(f"rank 1000: {selected[-1]['name']}")
    print(f"rank 1001: {selected_part2[0]['name']}")
    print(f"rank 2000: {selected_part2[-1]['name']}")
    print(f"primary-source names in output: {sum(row['source_priority'] == 1 for row in selected)}")


def main() -> None:
    write_outputs(ranked_rows())


if __name__ == "__main__":
    main()
