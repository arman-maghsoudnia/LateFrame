#!/usr/bin/env python3

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

def parse_pcap(input_file):
    # Read packet timestamps from the capture and store inter-arrival times in ms.
    pcap_path = Path(input_file)
    if not pcap_path.exists():
        raise FileNotFoundError(f"missing input file: {pcap_path}")

    cmd = [
        "tshark",
        "-r",
        str(pcap_path),
        "-T",
        "fields",
        "-e",
        "frame.time_epoch",
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("tshark is required but was not found in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"tshark failed for {pcap_path}:\n{exc.stderr.strip()}"
        ) from exc

    timestamps = [float(line) for line in result.stdout.splitlines() if line.strip()]
    if len(timestamps) < 2:
        raise ValueError(
            f"{pcap_path} does not contain enough packets to compute inter-arrival times"
        )

    inter_arrivals_ms = np.diff(np.asarray(timestamps, dtype=np.float64)) * 1000.0
    return inter_arrivals_ms.tolist()

def compute_mae(baseline, file2):
    # check if files have the same length
    if len(baseline) != len(file2):
        print("Files have different lengths")
        print(f"Baseline length: {len(baseline)}, File2 length: {len(file2)}")
        print("Truncating to the shorter length for comparison")
        min_len = min(len(baseline), len(file2))
        baseline = baseline[:min_len]
        file2 = file2[:min_len]
    # Compute MAE of inter arrival times
    baseline_arr = np.asarray(baseline, dtype=np.float64)
    file2_arr = np.asarray(file2, dtype=np.float64)
    return [float(np.mean(np.abs(baseline_arr - file2_arr))), baseline_arr, file2_arr]

def compare_pcap_files(baseline, file2, title):
    # Compute MAE and MAPE
    mae, baseline_arr, file2_arr = compute_mae(baseline, file2)
    #mape = compute_mape(baseline, file2)
    print(f"{title} - MAE: {mae:.6f} ms")

    # plot error over time
    plt.figure(figsize=(10, 6))
    plt.plot(np.abs(baseline_arr - file2_arr), label="Error", alpha=0.7)
    plt.xlabel("Packet Index")
    plt.ylabel("Absolute Inter-arrival Time Difference [ms]")
    plt.title(f"Absolute Inter-arrival Time Differences: Baseline vs {title}")
    plt.legend()
    plt.grid()
    plt.tight_layout()
    plt.savefig(f"docs/CPU_pinning/comparison_{title.replace(' ', '_')}.png")    

if __name__ == "__main__":
    # Parse input files
    directory = "comparison-data/CPU_pinning/"
    baseline_file = directory + "original.pcap"
    pinned_file = directory + "replay_pin.pcap"
    no_pin_file = directory + "replay_NOpin.pcap"
    baseline_data = parse_pcap(baseline_file)
    pinned_data = parse_pcap(pinned_file)
    no_pin_data = parse_pcap(no_pin_file)

    # Compare files
    compare_pcap_files(baseline_data, pinned_data, "Pinned CPU")
    compare_pcap_files(baseline_data, no_pin_data, "Unpinned CPU")