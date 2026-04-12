import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True)
class QualityCommandSpec:
    slug: str
    title: str
    manage_args: tuple[str, ...]


QUALITY_COMMAND_SPECS: tuple[QualityCommandSpec, ...] = (
    QualityCommandSpec("django_check", "Django system check", ("check",)),
    QualityCommandSpec("preferences_and_i18n", "Preferences and language tests", ("test", "inventory.tests.TimerAndPreferenceViewTests", "--noinput")),
    QualityCommandSpec("performance", "Lightweight performance tests", ("test", "inventory.tests.LightweightPerformanceTests", "--noinput")),
    QualityCommandSpec("admin_procedures", "Admin procedure tests", ("test", "inventory.tests.AdminProcedureTests", "--noinput")),
    QualityCommandSpec("web_roles", "Web role enforcement tests", ("test", "inventory.tests.RoleEnforcementWebTests", "--noinput")),
    QualityCommandSpec("api_roles", "API role and CRUD tests", ("test", "inventory.tests.InventoryApiTests", "--noinput")),
    QualityCommandSpec("backup", "Backup command tests", ("test", "inventory.tests.BackupCommandTests", "--noinput")),
)


def _quality_report_path() -> Path:
    return Path(getattr(settings, "QUALITY_REPORT_PATH"))


def load_quality_report() -> dict | None:
    path = _quality_report_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_quality_report(payload: dict) -> Path:
    path = _quality_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _parse_tests_ran(output: str) -> int:
    match = re.search(r"Ran (\d+) tests? in", output)
    return int(match.group(1)) if match else 0


def _parse_status(returncode: int, output: str) -> str:
    if returncode == 0:
        return "passed"
    if "FAILED" in output:
        return "failed"
    return "error"


def _trim_output(output: str, *, max_lines: int = 20) -> list[str]:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return lines[-max_lines:]


def generate_quality_report() -> dict:
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    python_executable = sys.executable
    started_at = timezone.now()
    results = []

    for spec in QUALITY_COMMAND_SPECS:
        started = perf_counter()
        command = [python_executable, str(manage_py), *spec.manage_args]
        completed = subprocess.run(
            command,
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
        )
        elapsed = perf_counter() - started
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        tests_ran = _parse_tests_ran(output)
        results.append(
            {
                "slug": spec.slug,
                "title": spec.title,
                "command": " ".join(command),
                "status": _parse_status(completed.returncode, output),
                "returncode": completed.returncode,
                "duration_seconds": round(elapsed, 3),
                "tests_ran": tests_ran,
                "output_tail": _trim_output(output),
            }
        )

    ended_at = timezone.now()
    passed_commands = sum(1 for item in results if item["status"] == "passed")
    total_tests_ran = sum(item["tests_ran"] for item in results)
    payload = {
        "generated_at": ended_at.isoformat(),
        "started_at": started_at.isoformat(),
        "python_executable": python_executable,
        "report_path": str(_quality_report_path()),
        "summary": {
            "total_commands": len(results),
            "passed_commands": passed_commands,
            "failed_commands": len(results) - passed_commands,
            "total_tests_ran": total_tests_ran,
            "overall_status": "passed" if passed_commands == len(results) else "failed",
        },
        "checks": results,
    }
    save_quality_report(payload)
    return payload
