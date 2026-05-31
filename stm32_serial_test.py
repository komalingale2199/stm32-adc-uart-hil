"""
stm32_serial_test.py
--------------------
Tests JSON telemetry streamed by a Wokwi STM32 simulation (or real hardware)
over a serial port at 115200 baud.

Usage:
    # Real hardware
    python stm32_serial_test.py --port COM3          # Windows
    python stm32_serial_test.py --port /dev/ttyUSB0  # Linux/Mac

    # No hardware — generate synthetic data instead
    python stm32_serial_test.py --mock

Dependencies:  pyserial  (pip install pyserial)
               Everything else is Python stdlib.
"""

import argparse
import json
import math
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── pyserial is only needed when talking to real hardware ────────────────────
try:
    import serial
except ImportError:
    serial = None  # Will raise a helpful error later if --mock is not used

# ── Test parameters ──────────────────────────────────────────────────────────
BAUD_RATE        = 115200   # Must match the firmware setting
TARGET_SAMPLES   = 20       # How many JSON lines we want to collect
COLLECTION_TIMEOUT = 15.0   # Seconds to wait before giving up
MAX_TIMESTAMP_GAP  = 600    # Max allowed gap in ms between consecutive "ts" fields

# ── Mock parameters ──────────────────────────────────────────────────────────
MOCK_INTERVAL    = 0.5      # Seconds between fake packets (mirrors 500 ms firmware loop)
MOCK_START_TS    = 1000     # Starting timestamp value (ms) for synthetic data


# ────────────────────────────────────────────────────────────────────────────
# Mock serial source
# ────────────────────────────────────────────────────────────────────────────

class MockSerial:
    """
    Mimics a pyserial Serial object well enough for our reader loop.
    A background thread pushes newline-terminated JSON bytes into a buffer;
    readline() blocks until a full line is ready, just like real serial.
    """

    def __init__(self):
        self._buf        = b""          # Accumulator for partial lines
        self._lock       = threading.Lock()
        self._data_ready = threading.Event()
        self._stop       = threading.Event()
        self._lines      = []           # Queue of complete lines waiting to be read

        # Start the background generator thread
        self._thread = threading.Thread(target=self._generate, daemon=True)
        self._thread.start()

    def _generate(self):
        """Produces one synthetic JSON packet every MOCK_INTERVAL seconds."""
        ts = MOCK_START_TS
        sample_num = 0

        while not self._stop.is_set():
            # Synthesise plausible sensor readings
            raw  = random.randint(200, 300)          # 12-bit ADC raw count (0–4095)
            mv   = int(raw * 3300 / 4095)            # Convert to millivolts (3.3 V ref)
            # Simulate a temperature that drifts slowly around 25 °C
            temp = round(25.0 + 5.0 * math.sin(sample_num * 0.4) + random.uniform(-0.5, 0.5), 1)

            packet = {
                "ts":     ts,
                "raw":    raw,
                "mv":     mv,
                "temp":   temp,
                "status": "OK",   # Mock always sends OK; change to test failure cases
            }

            line = (json.dumps(packet) + "\n").encode("utf-8")

            with self._lock:
                self._lines.append(line)
                self._data_ready.set()   # Signal that readline() can return

            ts         += int(MOCK_INTERVAL * 1000)  # Advance mock timestamp by 500 ms
            sample_num += 1
            time.sleep(MOCK_INTERVAL)

    def readline(self):
        """Block until a full line is available, then return and remove it."""
        while True:
            with self._lock:
                if self._lines:
                    line = self._lines.pop(0)           # FIFO order
                    if not self._lines:
                        self._data_ready.clear()        # Nothing left; reset flag
                    return line
            # No line yet — wait efficiently instead of busy-spinning
            self._data_ready.wait(timeout=0.1)

    def close(self):
        """Signal the generator thread to exit."""
        self._stop.set()


# ────────────────────────────────────────────────────────────────────────────
# Data collection
# ────────────────────────────────────────────────────────────────────────────

def collect_samples(source, n=TARGET_SAMPLES, timeout=COLLECTION_TIMEOUT):
    """
    Read newline-terminated JSON objects from `source` until we have `n`
    valid samples or `timeout` seconds have elapsed.

    Returns a list of parsed dicts.
    """
    samples      = []
    deadline     = time.monotonic() + timeout   # Absolute expiry time

    print(f"\n[*] Collecting {n} samples (timeout {timeout}s) …")

    while len(samples) < n:
        # Check whether we've run out of time before blocking on readline()
        if time.monotonic() > deadline:
            print(f"[!] Timeout reached after {len(samples)} samples.")
            break

        try:
            raw_line = source.readline()          # Blocks until data or mock tick
        except Exception as exc:
            print(f"[!] Serial read error: {exc}")
            break

        line = raw_line.strip()
        if not line:
            continue  # Skip empty lines (can appear at startup)

        try:
            packet = json.loads(line)             # Parse the JSON payload
        except json.JSONDecodeError as exc:
            print(f"[!] Bad JSON (skipped): {line!r}  → {exc}")
            continue  # Malformed line — skip, don't abort

        samples.append(packet)
        print(f"    [{len(samples):>2}/{n}]  ts={packet.get('ts')}  "
              f"mv={packet.get('mv')}  temp={packet.get('temp')}  "
              f"status={packet.get('status')}")

    return samples


# ────────────────────────────────────────────────────────────────────────────
# Assertions
# ────────────────────────────────────────────────────────────────────────────

def run_assertions(samples):
    """
    Execute the four test assertions against the collected sample list.
    Returns (passed_count, total_count, results_list).
    Each entry in results_list is a dict with keys: name, passed, detail.
    """
    results = []

    # ── Assertion 1: All status fields must be "OK" ──────────────────────────
    bad_status = [
        (i, s["status"]) for i, s in enumerate(samples)
        if s.get("status") != "OK"          # Flag any non-OK value
    ]
    results.append({
        "name":   'All "status" == "OK"',
        "passed": len(bad_status) == 0,
        "detail": f"{len(bad_status)} bad status values" if bad_status
                  else f"All {len(samples)} samples OK",
    })

    # ── Assertion 2: All mv values must be in [100, 3200] ────────────────────
    bad_mv = [
        (i, s["mv"]) for i, s in enumerate(samples)
        if not (100 <= s.get("mv", -1) <= 3200)   # Outside the valid ADC output range
    ]
    results.append({
        "name":   "All mv in [100, 3200]",
        "passed": len(bad_mv) == 0,
        "detail": f"Out-of-range mv at samples {[i for i, _ in bad_mv]}" if bad_mv
                  else f"All {len(samples)} mv values in range",
    })

    # ── Assertion 3: All temp values must be in [-40, 125] ───────────────────
    bad_temp = [
        (i, s["temp"]) for i, s in enumerate(samples)
        if not (-40 <= s.get("temp", -999) <= 125)  # Sensor operating range
    ]
    results.append({
        "name":   "All temp in [-40, 125]",
        "passed": len(bad_temp) == 0,
        "detail": f"Out-of-range temp at samples {[i for i, _ in bad_temp]}" if bad_temp
                  else f"All {len(samples)} temp values in range",
    })

    # ── Assertion 4: No timestamp gap > 600 ms ───────────────────────────────
    gaps = []
    for i in range(1, len(samples)):
        gap = samples[i].get("ts", 0) - samples[i - 1].get("ts", 0)
        if gap > MAX_TIMESTAMP_GAP:                 # Missed packet or firmware stall
            gaps.append((i, gap))

    results.append({
        "name":   f"No ts gap > {MAX_TIMESTAMP_GAP} ms",
        "passed": len(gaps) == 0,
        "detail": f"Large gaps at transitions {[(i, g) for i, g in gaps]}" if gaps
                  else f"All {len(samples) - 1} transitions within {MAX_TIMESTAMP_GAP} ms",
    })

    passed = sum(1 for r in results if r["passed"])
    return passed, len(results), results


# ────────────────────────────────────────────────────────────────────────────
# Report
# ────────────────────────────────────────────────────────────────────────────

def print_results(passed, total, results):
    """Pretty-print each assertion result, then the final summary line."""
    print("\n" + "─" * 54)
    print("  TEST RESULTS")
    print("─" * 54)

    for r in results:
        status_tag = "✓ PASS" if r["passed"] else "✗ FAIL"
        print(f"  {status_tag}  {r['name']}")
        print(f"          {r['detail']}")   # Indented detail line

    print("─" * 54)
    # Final summary mirrors pytest / TAP convention
    summary = f"PASSED {passed}/{total} assertions"
    print(f"  {'✓' if passed == total else '✗'} {summary}")
    print("─" * 54 + "\n")


def save_report(samples, passed, total, results, path="test_report.json"):
    """
    Write a JSON report file that includes:
      - run metadata (date/time, verdict)
      - assertion outcomes
      - every raw sample received
    """
    report = {
        "run_at":     datetime.now().isoformat(timespec="seconds"),  # e.g. 2025-01-15T14:32:01
        "summary":    f"PASSED {passed}/{total} assertions",
        "all_passed": passed == total,
        "assertions": results,          # List of {name, passed, detail}
        "samples":    samples,          # Raw JSON objects from the device
    }

    output_path = Path(path)
    output_path.write_text(
        json.dumps(report, indent=2),   # Human-readable indentation
        encoding="utf-8",
    )
    print(f"[*] Report saved → {output_path.resolve()}")


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="STM32 JSON serial tester (Wokwi or real hardware)",
    )
    parser.add_argument(
        "--port", "-p",
        help="Serial port to open, e.g. COM3 or /dev/ttyUSB0",
    )
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="Use synthetic data instead of a real serial port",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=TARGET_SAMPLES,
        help=f"Number of samples to collect (default: {TARGET_SAMPLES})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=COLLECTION_TIMEOUT,
        help=f"Collection timeout in seconds (default: {COLLECTION_TIMEOUT})",
    )
    parser.add_argument(
        "--report",
        default="test_report.json",
        help="Output report filename (default: test_report.json)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # ── Open the data source ─────────────────────────────────────────────────
    if args.mock:
        print("[*] Mock mode — generating synthetic serial data")
        source = MockSerial()
    else:
        if not args.port:
            print("[!] Error: specify --port <device> or use --mock", file=sys.stderr)
            sys.exit(1)
        if serial is None:
            print("[!] pyserial not installed. Run: pip install pyserial", file=sys.stderr)
            sys.exit(1)

        print(f"[*] Opening {args.port} @ {BAUD_RATE} baud …")
        try:
            # timeout=1 means readline() returns after 1 s even with no data,
            # so our deadline check in collect_samples() stays responsive.
            source = serial.Serial(args.port, BAUD_RATE, timeout=1)
        except serial.SerialException as exc:
            print(f"[!] Could not open port: {exc}", file=sys.stderr)
            sys.exit(1)

    # ── Collect, assert, report ──────────────────────────────────────────────
    try:
        samples = collect_samples(source, n=args.samples, timeout=args.timeout)
    finally:
        source.close()   # Always close/stop, even on keyboard interrupt

    if not samples:
        print("[!] No samples collected — nothing to test.")
        sys.exit(1)

    passed, total, results = run_assertions(samples)
    print_results(passed, total, results)
    save_report(samples, passed, total, results, path=args.report)

    # Exit code 0 = all passed, 1 = at least one failure (CI-friendly)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
