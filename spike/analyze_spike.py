"""Summarise spike/runs/<label>/<variant>/ into one table per label and variant.

Run: .venv/bin/python -I spike/analyze_spike.py [runs_dir]
"""
import csv
import math
import re
import statistics
import sys
from pathlib import Path

P95_LIMIT_S = 5.0
SWAP_TOLERANCE_MB = 1.0   # swapusage is printed with 0.01 MB resolution; allow rounding noise


def to_mb(text):
    m = re.match(r"\s*([\d.]+)\s*([KMG]?)B?", text or "")
    if not m:
        return None
    n = float(m.group(1))
    return n * {"K": 1 / 1024, "M": 1.0, "G": 1024.0, "": 1.0}[m.group(2)]


def swap_used_mb(path):
    m = re.search(r"used = ([\d.]+)M", path.read_text()) if path.exists() else None
    return float(m.group(1)) if m else None


def swapouts(path):
    m = re.search(r"Swapouts:\s+(\d+)", path.read_text()) if path.exists() else None
    return int(m.group(1)) if m else None


def free_pct(path):
    m = re.search(r"free percentage:\s*(\d+)%", path.read_text()) if path.exists() else None
    return int(m.group(1)) if m else None


def p95(values):
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]   # nearest rank


def fmt(x, nd=1, unit=""):
    return "n/a" if x is None else f"{x:.{nd}f}{unit}"


def analyse(vdir):
    out = {}
    cold = (vdir / "cold_load.txt").read_text().strip() if (vdir / "cold_load.txt").exists() else None
    out["cold_s"] = float(cold) if cold and cold != "FAILED" else None

    lat, probs, errors = [], [], 0
    if (vdir / "latency.csv").exists():
        for r in csv.DictReader((vdir / "latency.csv").open()):
            if r["error"] or not r["client_ms"]:
                errors += 1
                continue
            lat.append(float(r["client_ms"]) / 1000)
            probs.append(float(r["noul"]))
    out["n"], out["errors"] = len(lat), errors
    out["median_s"] = statistics.median(lat) if lat else None
    out["p95_s"] = p95(lat) if lat else None
    out["max_s"] = max(lat) if lat else None
    out["p_min"], out["p_med"], out["p_max"] = (
        (min(probs), statistics.median(probs), max(probs)) if probs else (None,) * 3
    )

    rss, fp, fp_peak, swap = [], [], [], []
    if (vdir / "mem.csv").exists():
        for r in csv.DictReader((vdir / "mem.csv").open()):
            if r["rss_kb"]:
                rss.append(int(r["rss_kb"]) / 1024)
            for key, bucket in (("phys_footprint", fp), ("phys_footprint_peak", fp_peak), ("swap_used", swap)):
                v = to_mb(r[key])
                if v is not None:
                    bucket.append(v)
    out["rss_mb"] = max(rss) if rss else None
    out["fp_mb"] = max(fp) if fp else None
    out["fp_peak_mb"] = max(fp_peak) if fp_peak else None
    out["swap_peak_run_mb"] = max(swap) if swap else None
    out["samples"] = len(rss)

    # idle control run: no process, only system.csv (ts,swap_used,swapouts,free_pct)
    if (vdir / "system.csv").exists():
        sys_rows = list(csv.DictReader((vdir / "system.csv").open()))
        sys_swap = [v for v in (to_mb(r["swap_used"]) for r in sys_rows) if v is not None]
        if sys_swap:
            out["swap_peak_run_mb"] = max(sys_swap)
        out["samples"] = len(sys_rows)

    out["swap_before"] = swap_used_mb(vdir / "swap_before.txt")
    out["swap_after"] = swap_used_mb(vdir / "swap_after.txt")
    so_b, so_a = swapouts(vdir / "vm_stat_before.txt"), swapouts(vdir / "vm_stat_after.txt")
    out["swapouts_delta"] = so_a - so_b if so_a is not None and so_b is not None else None
    out["free_before"] = free_pct(vdir / "memory_pressure_before.txt")
    out["free_after"] = free_pct(vdir / "memory_pressure_after.txt")
    return out


def verdicts(o):
    sb, sa = o["swap_before"], o["swap_after"]
    swap_ok = None if sb is None or sa is None else sa <= sb + SWAP_TOLERANCE_MB
    p95_ok = None if o["p95_s"] is None else o["p95_s"] <= P95_LIMIT_S
    return swap_ok, p95_ok


def word(ok):
    return "n/a" if ok is None else ("PASS" if ok else "FAIL")


def main():
    runs = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "runs"
    found = False
    for label_dir in sorted(p for p in runs.iterdir() if p.is_dir() and p.name != "smoke") if runs.exists() else []:
        for vdir in sorted(p for p in label_dir.iterdir() if p.is_dir() and any(p.iterdir())):
            found = True
            o = analyse(vdir)
            swap_ok, p95_ok = verdicts(o)
            flat = o["p_max"] is not None and (o["p_max"] - o["p_min"]) < 0.10
            rows = [
                ("cold load (launch -> first answer)", fmt(o["cold_s"], 1, " s")),
                ("requests ok / errors", f"{o['n']} / {o['errors']}"),
                ("warm median", fmt(o["median_s"], 3, " s")),
                ("warm p95 (nearest rank)", fmt(o["p95_s"], 3, " s")),
                ("warm max", fmt(o["max_s"], 3, " s")),
                ("peak RSS (ps; may undercount GPU memory)", fmt(o["rss_mb"], 0, " MB")),
                ("peak phys footprint (sampled)", fmt(o["fp_mb"], 0, " MB")),
                ("peak phys footprint (kernel lifetime peak)", fmt(o["fp_peak_mb"], 0, " MB")),
                ("memory samples", str(o["samples"])),
                ("swap used before / after", f"{fmt(o['swap_before'], 1)} / {fmt(o['swap_after'], 1)} MB"),
                ("swap used, max during run", fmt(o["swap_peak_run_mb"], 1, " MB")),
                ("swapouts delta (pages, vm_stat)", "n/a" if o["swapouts_delta"] is None else str(o["swapouts_delta"])),
                ("memory_pressure free % before / after", f"{o['free_before']} / {o['free_after']}"),
                ("noul min / median / max", f"{fmt(o['p_min'], 4)} / {fmt(o['p_med'], 4)} / {fmt(o['p_max'], 4)}"
                                            + ("   WARNING: probabilities nearly flat" if flat else "")),
                ("CRITERION swap does not grow", word(swap_ok)),
                (f"CRITERION warm p95 <= {P95_LIMIT_S:g} s", word(p95_ok)),
                ("CRITERION Mac stayed usable", "____ (Dev fills in)"),
            ]
            print(f"\n### {label_dir.name} / {vdir.name}" + ("   (OBSERVATION ONLY, not a rule input)" if label_dir.name == "idle" else ""))
            print("| metric | value |\n|---|---|")
            for k, v in rows:
                print(f"| {k} | {v} |")
    if not found:
        print(f"no runs found under {runs}")


if __name__ == "__main__":
    main()
