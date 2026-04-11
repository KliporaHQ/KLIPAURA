from datetime import datetime, timezone
from pathlib import Path

_CORE_V1 = Path(__file__).resolve().parent.parent
CONTEXT_PATH = _CORE_V1 / "KLIPAURA_REBOOT_MASTER_CONTEXT.md"
LOG_PATH = _CORE_V1 / "outputs" / "last_run.log"
FINAL_VIDEO_PATH = _CORE_V1 / "outputs" / "final_publish" / "FINAL_VIDEO.mp4"


def detect_status():
    if not FINAL_VIDEO_PATH.exists():
        return "FAIL", "Pipeline did not complete"
    size = FINAL_VIDEO_PATH.stat().st_size
    if size > 500_000:
        return "SUCCESS", f"FINAL_VIDEO.mp4 generated ({size} bytes)"
    return "FAIL", f"FINAL_VIDEO.mp4 too small ({size} bytes)"


def extract_stage_failure():
    if not LOG_PATH.exists():
        return "UNKNOWN", "No log file"

    with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    last_stage = "UNKNOWN"
    error_line = "UNKNOWN"

    for line in lines:
        if "[1/7]" in line:
            last_stage = "[1/7]"
        elif "[2/7]" in line:
            last_stage = "[2/7]"
        elif "[3/7]" in line:
            last_stage = "[3/7]"
        elif "[4/7]" in line:
            last_stage = "[4/7]"
        elif "[5/7]" in line:
            last_stage = "[5/7]"
        elif "[6/7]" in line:
            last_stage = "[6/7]"
        elif "[7/7]" in line:
            last_stage = "[7/7]"

        if "FAIL FAST" in line:
            error_line = line.strip()

    return last_stage, error_line


def build_entry():
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    status, summary = detect_status()
    stage, error = extract_stage_failure()

    entry = f"""
---

### AUTO RUN UPDATE — {timestamp}

Status: {status}

Stage Reached: {stage}

Summary:
{summary}

Error:
{error}

Next Action:
{"Proceed to publish" if status == "SUCCESS" else "Fix stage " + stage}

---
"""
    return entry


def append_context():
    entry = build_entry()

    with open(CONTEXT_PATH, "a", encoding="utf-8") as f:
        f.write(entry)

    print("Context updated.")


if __name__ == "__main__":
    append_context()
