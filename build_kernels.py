"""Rebuild the knife-hunt search kernels.

Python rather than a shell script on purpose: this sandbox blocks nested
`bash <script>` invocations (it tries to pull in wsl.exe, which is on the Program
Blacklist), so a .sh here is unrunnable. `python build_kernels.py` works.

Why fast6 is linked STATICALLY
------------------------------
The OpenMP build adds libgomp-1.dll and libwinpthread-1.dll on top of the
libstdc++-6.dll / libgcc that fast and fast4 already need. `-static` removes the
whole DLL question, so the binary starts anywhere. Cost (measured 2026-09-18,
best of 3, 5 s wall clock, relative to fast4 serial):

    threads:        1        4        8
    dynamic    ~1.00x    2.02x    1.86x
    static     ~1.00x    2.01x    1.71x

So static costs a few percent at 8 threads and nothing at 1-4. The kernel only
parallelises 8 independent trials, so 4 threads is the sweet spot and going past
8 threads cannot help.

`-march=native` is kept because the search host is this machine. If the binary
ever has to run on a different CPU, drop it and rebuild.
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CXX = os.environ.get("CXX", r"D:\tmp\msys64\ucrt64\bin\g++.exe")
FLAGS = ["-O3", "-march=native", "-std=c++17"]
IN = os.path.join(ROOT, "runs", "khfast_smoke", "engine_input.txt")
TMP = os.path.join(ROOT, "runs", "speed2")


def build(out, src, extra):
    cmd = [CXX] + FLAGS + extra + ["-o", os.path.join(ROOT, out), os.path.join(ROOT, src)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    warn = [l for l in p.stderr.splitlines() if "deprecat" not in l and "pragma omp" not in l
            and not l.strip().startswith("|") and "In function" not in l]
    print("  %-32s rc=%d%s" % (out, p.returncode, ("  " + " ".join(warn)[:160]) if warn else ""))
    return p.returncode == 0


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def run(exe, out, attempts, threads=None):
    env = dict(os.environ, KH_MAX_ATTEMPTS=str(attempts))
    if threads:
        env["KH_THREADS"] = str(threads)
    subprocess.run([os.path.join(ROOT, exe), IN, os.path.join(TMP, out), "100000", "7411", "48"],
                   capture_output=True, text=True, env=env)
    return md5(os.path.join(TMP, out))


def main():
    print("building:")
    ok = build("calibrated_knife_hunt_fast.exe", "calibrated_knife_hunt_fast.cpp", [])
    ok &= build("calibrated_knife_hunt_fast4.exe", "calibrated_knife_hunt_fast4.cpp", [])
    ok &= build("calibrated_knife_hunt_fast6.exe", "calibrated_knife_hunt_fast6.cpp",
                ["-fopenmp", "-static"])
    ok &= build("calibrated_knife_hunt_fast6_dyn.exe", "calibrated_knife_hunt_fast6.cpp", ["-fopenmp"])
    if not ok:
        return 1

    print("regression (fast6 must equal fast4 byte for byte):")
    for n in (5000, 20000):
        ref = run("calibrated_knife_hunt_fast4.exe", "bk_ref.txt", n)
        row = "  N=%-6d fast4=%s" % (n, ref[:12])
        bad = False
        for th in (1, 4, 8):
            h = run("calibrated_knife_hunt_fast6.exe", "bk_out.txt", n, th)
            bad |= (h != ref)
            row += "  t%d=%s" % (th, "OK" if h == ref else "MISMATCH")
        print(row)
        if bad:
            return 1
    print("all byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
