"""Remove only this executor's abandoned task containers before service startup."""
import subprocess

ids = subprocess.check_output(
    ["docker", "ps", "-aq", "--filter", "label=astrbot.code-running=true"], text=True
).split()
if ids:
    subprocess.run(["docker", "rm", "-f", *ids], check=True)
