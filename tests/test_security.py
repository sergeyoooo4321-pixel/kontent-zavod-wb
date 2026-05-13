from pathlib import Path
import subprocess


SECRET_PATTERNS = [
    "sk-" + "aitunnel-",
    "AAH" + "tdqi1",
    "YC" + "AJ",
    "YC" + "P5",
    "eyJ" + "hbGciOi",
]


def test_no_known_secret_literals_in_repo():
    root = Path(__file__).resolve().parents[1]
    checked = []
    tracked_files = subprocess.check_output(
        ["git", "ls-files"],
        cwd=root,
        encoding="utf-8",
    ).splitlines()
    for rel_path in tracked_files:
        path = root / rel_path
        if path.suffix.lower() in {".pyc", ".db", ".sqlite3", ".zip"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        checked.append(path)
        for pattern in SECRET_PATTERNS:
            assert pattern not in text, f"secret-like literal found in {path}"
    assert checked
