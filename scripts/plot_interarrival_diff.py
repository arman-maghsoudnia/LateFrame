#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


DEFAULT_ORIGINAL_PCAP = Path("comparison-data/ping-test.pcap")
DEFAULT_REPLAYED_PCAP = Path("comparison-data/replayed/ping-test-replayed-result.pcap")
DEFAULT_OUTPUT_DIR = Path("docs/interarrival-diff")
DENSITY_BIN_WIDTH_MS = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare original and replayed PCAP inter-arrival times by plotting "
            "their packet-by-packet difference."
        )
    )
    parser.add_argument(
        "--original",
        type=Path,
        default=DEFAULT_ORIGINAL_PCAP,
        help=f"Original PCAP path (default: {DEFAULT_ORIGINAL_PCAP}).",
    )
    parser.add_argument(
        "--replayed",
        type=Path,
        default=DEFAULT_REPLAYED_PCAP,
        help=f"Replayed PCAP path (default: {DEFAULT_REPLAYED_PCAP}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save the generated plots (default: {DEFAULT_OUTPUT_DIR}).",
    )
    return parser.parse_args()


def load_timestamps_from_pcap(pcap_path: Path) -> list[float]:
    if not pcap_path.exists():
        raise RuntimeError(f"missing input file: {pcap_path}")
    if not os.access(pcap_path, os.R_OK):
        raise RuntimeError(
            f"cannot read {pcap_path}. Adjust file permissions or run with sufficient privileges."
        )

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
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("tshark is required but was not found in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"tshark failed for {pcap_path}:\n{exc.stderr.strip()}"
        ) from exc

    timestamps = [float(line) for line in result.stdout.splitlines() if line.strip()]
    if len(timestamps) < 2:
        raise RuntimeError(f"{pcap_path} does not contain enough packets.")
    return timestamps


def compute_interarrivals_ms(timestamps_s: list[float]) -> np.ndarray:
    return np.diff(np.array(timestamps_s, dtype=np.float64)) * 1000.0


def build_bin_edges(values: np.ndarray, bin_width_ms: float) -> np.ndarray:
    if bin_width_ms <= 0:
        raise ValueError("bin width must be positive.")

    x_min = float(np.min(values))
    x_max = float(np.max(values))
    start = np.floor(x_min / bin_width_ms) * bin_width_ms
    stop = np.ceil(x_max / bin_width_ms) * bin_width_ms
    num_bins = max(1, int(np.ceil((stop - start) / bin_width_ms)))
    return np.linspace(start, start + num_bins * bin_width_ms, num_bins + 1)


def gaussian_kde(values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    samples = np.asarray(values, dtype=np.float64)
    n = len(samples)
    if n < 2:
        return np.zeros_like(grid)

    std = float(np.std(samples, ddof=1))
    if std == 0.0:
        std = 1e-6

    bandwidth = 1.06 * std * (n ** (-1.0 / 5.0))
    bandwidth = max(bandwidth, 1e-4)

    scaled = (grid[:, None] - samples[None, :]) / bandwidth
    density = np.exp(-0.5 * scaled * scaled).sum(axis=1)
    density /= n * bandwidth * np.sqrt(2.0 * np.pi)
    return density


def print_stats(original: np.ndarray, replayed: np.ndarray, diff: np.ndarray) -> None:
    print(f"original_count={len(original)}")
    print(f"replayed_count={len(replayed)}")
    print(f"compared_count={len(diff)}")
    print(f"original_mean_ms={np.mean(original):.9f}")
    print(f"replayed_mean_ms={np.mean(replayed):.9f}")
    print(f"diff_mean_ms={np.mean(diff):.9f}")
    print(f"diff_std_ms={np.std(diff, ddof=1):.9f}")
    print(f"diff_min_ms={np.min(diff):.9f}")
    print(f"diff_max_ms={np.max(diff):.9f}")
    print(f"diff_abs_mean_ms={np.mean(np.abs(diff)):.9f}")


def plot_diff_density(diff_ms: np.ndarray, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.8))

    edges = build_bin_edges(diff_ms, DENSITY_BIN_WIDTH_MS)
    x_min = float(np.min(diff_ms))
    x_max = float(np.max(diff_ms))
    x_pad = max(0.01, (x_max - x_min) * 0.08)
    grid = np.linspace(x_min - x_pad, x_max + x_pad, 1200)

    mean_ms = float(np.mean(diff_ms))
    std_ms = float(np.std(diff_ms, ddof=1))
    label = f"diff (mean={mean_ms:.4f} ms, std={std_ms:.4f} ms)"

    ax.hist(
        diff_ms,
        bins=edges,
        density=True,
        alpha=0.55,
        color="#9fd3ff",
        edgecolor="white",
        linewidth=0.7,
        label=label,
    )
    ax.plot(
        grid,
        gaussian_kde(diff_ms, grid),
        color="#107ed6",
        linewidth=1.5,
    )
    ax.axvline(0.0, color="black", linestyle="--", linewidth=1.0, alpha=0.8)

    ax.set_title("Inter-arrival Difference Density")
    ax.set_xlabel("original - replayed inter-arrival [ms]")
    ax.set_ylabel("Density Function")
    ax.grid(True, alpha=0.45, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=True)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_diff_heartbeat(diff_ms: np.ndarray, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.8))

    packet_index = np.arange(1, len(diff_ms) + 1)

    ax.plot(
        packet_index,
        diff_ms,
        color="#8ab4f8",
        linewidth=0.8,
        alpha=0.7,
    )
    ax.scatter(
        packet_index,
        diff_ms,
        s=10,
        color="#1a73e8",
        alpha=0.85,
        edgecolors="none",
    )
    ax.axhline(0.0, color="black", linestyle="--", linewidth=1.0, alpha=0.8)

    ax.set_title("Inter-arrival Difference Per Packet")
    ax.set_xlabel("Packet index")
    ax.set_ylabel("original - replayed inter-arrival [ms]")
    ax.grid(True, alpha=0.35, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def main() -> int:
    args = parse_args()

    try:
        original_interarrivals = compute_interarrivals_ms(load_timestamps_from_pcap(args.original))
        replayed_interarrivals = compute_interarrivals_ms(load_timestamps_from_pcap(args.replayed))
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    compared_count = min(len(original_interarrivals), len(replayed_interarrivals))
    if compared_count == 0:
        print("Error: no inter-arrival samples to compare.", file=sys.stderr)
        return 1

    original_aligned = original_interarrivals[:compared_count]
    replayed_aligned = replayed_interarrivals[:compared_count]
    diff_ms = original_aligned - replayed_aligned

    print_stats(original_aligned, replayed_aligned, diff_ms)

    density_output = args.output_dir / "ping-replay-interarrival-diff-density.png"
    heartbeat_output = args.output_dir / "ping-replay-interarrival-diff-heartbeat.png"

    plot_diff_density(diff_ms, density_output)
    plot_diff_heartbeat(diff_ms, heartbeat_output)

    print(f"Saved plot to {density_output}")
    print(f"Saved plot to {heartbeat_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
