"""Alle Tests: Engine-Module (dev/organizer/test_*.py), App-Ende-zu-Ende-Test und die
Prüfung, ob DesktopSorter.pyw zu den Quellen passt.

    python dev/run_tests.py            # alles
    python dev/run_tests.py --quick    # ohne App-Test
"""
import os
import subprocess
import sys
import time
from pathlib import Path

DEV = Path(__file__).resolve().parent


def run(cmd, cwd, label):
    t = time.time()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    status = "OK " if r.returncode == 0 else "FEHLER"
    print(f"  {status} {label:<44} {time.time() - t:5.1f} s")
    if r.returncode:
        print("\n".join((r.stdout + r.stderr).splitlines()[-25:]))
    return r.returncode == 0


def main():
    py = sys.executable
    ok = run([py, str(DEV / "assemble.py"), "--check"], DEV.parent, "DesktopSorter.pyw aktuell")
    print(f"Engine-Tests ({sys.version.split()[0]}):")
    for t in sorted((DEV / "organizer").glob("test_*.py")):
        ok &= run([py, t.name], DEV / "organizer", t.name)
    if "--quick" not in sys.argv:
        print("App-Test:")
        ok &= run([py, str(DEV / "tests" / "test_app_organizer.py")], DEV.parent,
                  "test_app_organizer.py")
    print("\nALLE TESTS BESTANDEN" if ok else "\nES GIBT FEHLER")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
