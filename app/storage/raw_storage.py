import json
from pathlib import Path
from datetime import datetime


def save_json(
    data,
    category: str,
    source: str
):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    directory = (
        Path("data")
        / category
        / source
    )

    directory.mkdir(
        parents=True,
        exist_ok=True
    )

    file_path = (
        directory
        / f"{timestamp}.json"
    )

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    return file_path