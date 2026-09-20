from __future__ import annotations

from collections import defaultdict
import csv
import io
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "three_letter_names" / "research" / "official"
OUT_DIR = ROOT / "four_letter_names"
NAME_RE = re.compile(r"[A-Za-z]{4}\Z")

PLACEHOLDER_NAMES = {
    "Baby",
    "Boy",
    "Girl",
    "Male",
    "Name",
}


def clean_name(raw: str) -> str | None:
    name = raw.strip().title()
    if not NAME_RE.fullmatch(name):
        return None
    if name in PLACEHOLDER_NAMES:
        return None
    return name


def existing_names() -> set[str]:
    names: set[str] = set()
    for filename in (
        "four_letter_names_1000_lowercase.txt",
        "four_letter_names_1000_part2_lowercase.txt",
    ):
        path = ROOT / filename
        if path.exists():
            names.update(line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return names


def ranked_ssa_rows() -> list[dict[str, object]]:
    counts: dict[str, int] = defaultdict(int)
    zip_path = OFFICIAL / "ssa_mirror_names.zip"
    archive_years = set()

    with zipfile.ZipFile(zip_path) as archive:
        for member in sorted(archive.namelist()):
            match = re.fullmatch(r"yob(\d{4})\.txt", member)
            if not match:
                continue
            archive_years.add(int(match.group(1)))
            text = archive.read(member).decode("utf-8-sig")
            for name, _sex, number in csv.reader(io.StringIO(text)):
                cleaned = clean_name(name)
                if cleaned:
                    counts[cleaned] += int(number)

    for path in sorted(OFFICIAL.glob("ssa_yob*.txt")):
        year = int(re.search(r"\d{4}", path.name).group())
        if year in archive_years or path.stat().st_size < 1000:
            continue
        for name, _sex, number in csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
            cleaned = clean_name(name)
            if cleaned:
                counts[cleaned] += int(number)

    rows = [
        {
            "rank": 0,
            "name": name,
            "lowercase": name.lower(),
            "ssa_us_count": count,
            "source": "U.S. SSA national baby-name data, 1880-2024 local mirror",
        }
        for name, count in counts.items()
    ]
    rows.sort(key=lambda row: (-int(row["ssa_us_count"]), str(row["name"])))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def write_outputs() -> None:
    excluded = existing_names()
    rows = [row for row in ranked_ssa_rows() if str(row["lowercase"]) not in excluded]
    if len(rows) < 1000:
        raise ValueError(f"Only {len(rows)} additional four-letter names were found.")

    selected = rows[:1000]
    names = [str(row["name"]) for row in selected]
    lower = [str(row["lowercase"]) for row in selected]

    (ROOT / "four_letter_names_1000_part3.txt").write_text(
        "\n".join(names) + "\n", encoding="utf-8"
    )
    (ROOT / "four_letter_names_1000_part3_lowercase.txt").write_text(
        "\n".join(lower) + "\n", encoding="utf-8"
    )

    OUT_DIR.mkdir(exist_ok=True)
    with (OUT_DIR / "name_rank_evidence_part3_ssa.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)

    print(f"excluded existing four-letter names: {len(excluded)}")
    print(f"additional source-backed names available: {len(rows)}")
    print(f"wrote {len(selected)} part-3 names")
    print(f"first: {selected[0]['name']} ({selected[0]['ssa_us_count']})")
    print(f"last: {selected[-1]['name']} ({selected[-1]['ssa_us_count']})")


if __name__ == "__main__":
    write_outputs()
