#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


DEFAULT_ORIGINAL_PCAP = Path("comparison-data/generated/ping-test.pcap")
DEFAULT_REPLAY_DIR = Path("comparison-data/replay")
DEFAULT_OUTPUT_DIR = Path("docs/replay")
DENSITY_BIN_WIDTH_MS = 0.005
OUTPUT_DPI = 600
AGGREGATE_FIGSIZE = (12, 13.5)
INDIVIDUAL_FIGSIZE = (10, 4.8)
HEARTBEAT_FIGSIZE = (12, 4.8)


@dataclass
class ReplaySeries:
    key: str
    label: str
    replayed_path: Path
    replayed_interarrivals_ms: np.ndarray
    diff_ms: np.ndarray
    original_aligned_ms: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one original PCAP against every replay PCAP in a directory by "
            "plotting packet-by-packet inter-arrival differences."
        )
    )
    parser.add_argument(
        "--original",
        type=Path,
        default=DEFAULT_ORIGINAL_PCAP,
        help=f"Original PCAP path (default: {DEFAULT_ORIGINAL_PCAP}).",
    )
    parser.add_argument(
        "--replay-dir",
        type=Path,
        default=DEFAULT_REPLAY_DIR,
        help=f"Directory containing replay PCAPs (default: {DEFAULT_REPLAY_DIR}).",
    )
    parser.add_argument(
        "--replayed-dir",
        dest="replay_dir",
        type=Path,
        help="Backward-compatible alias for --replay-dir.",
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


def replay_key_from_path(path: Path) -> str:
    name = path.stem
    prefix = "ping-test-replayed-result-"
    if name.startswith(prefix):
        return name[len(prefix):]
    if name == "ping-test-replayed-result":
        return "replayed"
    return name


def replay_label_from_key(key: str) -> str:
    label_map = {
        "timerfd": "timerfd",
        "spin50": "nanosleep spin 50us",
        "spin100": "nanosleep spin 100us",
        "replayed": "replayed",
    }
    return label_map.get(key, key.replace("-", " "))


def stats_dict(original: np.ndarray, replayed: np.ndarray, diff: np.ndarray) -> dict[str, float]:
    return {
        "original_count": len(original),
        "replayed_count": len(replayed),
        "compared_count": len(diff),
        "original_mean_ms": float(np.mean(original)),
        "replayed_mean_ms": float(np.mean(replayed)),
        "diff_mean_ms": float(np.mean(diff)),
        "diff_std_ms": float(np.std(diff, ddof=1)),
        "diff_min_ms": float(np.min(diff)),
        "diff_max_ms": float(np.max(diff)),
        "diff_abs_mean_ms": float(np.mean(np.abs(diff))),
    }


def print_stats(series: ReplaySeries) -> None:
    stats = stats_dict(
        series.original_aligned_ms,
        series.replayed_interarrivals_ms,
        series.diff_ms,
    )
    print(f"[{series.key}] replayed={series.replayed_path}")
    for key, value in stats.items():
        if key.endswith("_count"):
            print(f"{key}={int(value)}")
        else:
            print(f"{key}={value:.9f}")


def load_series(original_pcap: Path, replay_dir: Path) -> list[ReplaySeries]:
    replayed_paths = sorted(replay_dir.glob("*.pcap"))
    if not replayed_paths:
        raise RuntimeError(f"no replay PCAP files found in {replay_dir}")

    original_interarrivals = compute_interarrivals_ms(load_timestamps_from_pcap(original_pcap))
    series_list: list[ReplaySeries] = []

    for replayed_path in replayed_paths:
        replayed_interarrivals = compute_interarrivals_ms(load_timestamps_from_pcap(replayed_path))
        compared_count = min(len(original_interarrivals), len(replayed_interarrivals))
        if compared_count == 0:
            raise RuntimeError(f"no inter-arrival samples to compare for {replayed_path}")

        original_aligned = original_interarrivals[:compared_count]
        replayed_aligned = replayed_interarrivals[:compared_count]
        diff_ms = original_aligned - replayed_aligned
        key = replay_key_from_path(replayed_path)

        series_list.append(
            ReplaySeries(
                key=key,
                label=replay_label_from_key(key),
                replayed_path=replayed_path,
                replayed_interarrivals_ms=replayed_aligned,
                diff_ms=diff_ms,
                original_aligned_ms=original_aligned,
            )
        )

    return series_list


def plot_density_axis(ax: plt.Axes, diff_ms: np.ndarray, title: str) -> None:
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

    ax.set_title(title)
    ax.set_xlabel("original - replayed inter-arrival [ms]")
    ax.set_ylabel("Density Function")
    ax.grid(True, alpha=0.45, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=True)


def plot_heartbeat_axis(ax: plt.Axes, diff_ms: np.ndarray, title: str) -> None:
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

    ax.set_title(title)
    ax.set_xlabel("Packet index")
    ax.set_ylabel("original - replayed inter-arrival [ms]")
    ax.grid(True, alpha=0.35, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_individual_density(series: ReplaySeries, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=INDIVIDUAL_FIGSIZE)
    plot_density_axis(ax, series.diff_ms, f"Inter-arrival Difference Density ({series.label})")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=OUTPUT_DPI)
    plt.close(fig)


def plot_individual_heartbeat(series: ReplaySeries, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=HEARTBEAT_FIGSIZE)
    plot_heartbeat_axis(ax, series.diff_ms, f"Inter-arrival Difference Per Packet ({series.label})")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=OUTPUT_DPI)
    plt.close(fig)


def plot_aggregate_density(series_list: list[ReplaySeries], output_path: Path) -> None:
    fig, axes = plt.subplots(len(series_list), 1, figsize=AGGREGATE_FIGSIZE)
    axes_array = np.atleast_1d(axes)

    for ax, series in zip(axes_array, series_list):
        plot_density_axis(ax, series.diff_ms, f"Inter-arrival Difference Density ({series.label})")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=OUTPUT_DPI)
    plt.close(fig)


def plot_aggregate_heartbeat(series_list: list[ReplaySeries], output_path: Path) -> None:
    fig, axes = plt.subplots(len(series_list), 1, figsize=AGGREGATE_FIGSIZE)
    axes_array = np.atleast_1d(axes)

    for ax, series in zip(axes_array, series_list):
        plot_heartbeat_axis(ax, series.diff_ms, f"Inter-arrival Difference Per Packet ({series.label})")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=OUTPUT_DPI)
    plt.close(fig)


def main() -> int:
    args = parse_args()

    try:
        series_list = load_series(args.original, args.replay_dir)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for series in series_list:
        print_stats(series)
        density_output = args.output_dir / "density" / f"ping-replay-interarrival-diff-density-{series.key}.png"
        heartbeat_output = args.output_dir / "heartbeat" / f"ping-replay-interarrival-diff-heartbeat-{series.key}.png"
        plot_individual_density(series, density_output)
        plot_individual_heartbeat(series, heartbeat_output)
        print(f"Saved plot to {density_output}")
        print(f"Saved plot to {heartbeat_output}")

    aggregate_heartbeat_output = args.output_dir / "ping-replay-interarrival-diff-heartbeat-aggregate.png"
    aggregate_density_output = args.output_dir / "ping-replay-interarrival-diff-density-aggregate.png"
    plot_aggregate_heartbeat(series_list, aggregate_heartbeat_output)
    plot_aggregate_density(series_list, aggregate_density_output)
    print(f"Saved plot to {aggregate_heartbeat_output}")
    print(f"Saved plot to {aggregate_density_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
