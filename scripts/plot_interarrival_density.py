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


# Hardcode the histogram bin width per series for the combined comparison plot.
COMPARISON_BIN_WIDTH_MS = {
    "lateframe-timerfd": 0.05,
    "lateframe-spin-50us": 0.05,
    "lateframe-spin-100us": 0.05,
    "ping": 0.05,
    "fping": 0.05,
}

# Hardcode the histogram bin width per series for the individual plots.
INDIVIDUAL_BIN_WIDTH_MS = {
    "lateframe-timerfd": 0.005,
    "lateframe-spin-50us": 0.005,
    "lateframe-spin-100us": 0.005,
    "ping": 0.05,
    "fping": 0.02,
}

ZOOMED_TRIMMED_BIN_WIDTH_MS = {
    "ping": 0.02,
    "fping": 0.01,
}

COLORS = {
    "lateframe-timerfd": "#12a8f0",
    "lateframe-spin-50us": "#0c88c2",
    "lateframe-spin-100us": "#7fd4ff",
    "ping": "#86bf10",
    "fping": "#ff5a1f",
}

FILLS = {
    "lateframe-timerfd": "#7ccbf3",
    "lateframe-spin-50us": "#5eb5e0",
    "lateframe-spin-100us": "#b6e8ff",
    "ping": "#b8dd67",
    "fping": "#f7a287",
}

UNIFIED_GRID_DPI = 600
UNIFIED_GRID_FIGSIZE = (20, 15.5)


@dataclass
class DensitySeries:
    key: str
    label: str
    pcap_path: Path
    interarrivals_ms: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot density histograms of packet inter-arrival times from comparison PCAP files."
    )
    parser.add_argument(
        "--series",
        action="append",
        required=True,
        metavar="KEY=LABEL=PCAP",
        help="PCAP series to plot. May be repeated.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to save the combined density figure.",
    )
    parser.add_argument(
        "--individual-output-dir",
        type=Path,
        required=True,
        help="Directory to save one density figure per series.",
    )
    parser.add_argument(
        "--zoomed-output-dir",
        type=Path,
        required=True,
        help="Directory to save trimmed zoom density figures.",
    )
    parser.add_argument(
        "--zoomed",
        action="append",
        default=[],
        metavar="KEY",
        help="Series key to also plot with the top and bottom 1 percent removed. May be repeated.",
    )
    parser.add_argument(
        "--unified-output",
        type=Path,
        required=True,
        help="Path to save the unified grid image.",
    )
    parser.add_argument(
        "--title",
        default="Inter-arrival Density Comparison",
        help="Plot title.",
    )
    return parser.parse_args()


def parse_series_spec(spec: str) -> tuple[str, str, Path]:
    parts = spec.split("=", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid --series value '{spec}', expected KEY=LABEL=PCAP")

    key, label, pcap = (part.strip() for part in parts)
    if not key or not label or not pcap:
        raise ValueError(f"invalid --series value '{spec}', expected KEY=LABEL=PCAP")

    return key, label, Path(pcap)


def load_timestamps_from_pcap(pcap_path: Path) -> list[float]:
    if not os.access(pcap_path, os.R_OK):
        raise RuntimeError(
            f"cannot read {pcap_path}. Adjust file permissions or run the script with sufficient privileges."
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

    timestamps = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        timestamps.append(float(line))

    if len(timestamps) < 2:
        raise RuntimeError(
            f"{pcap_path} does not contain enough packets to compute inter-arrival times."
        )

    return timestamps


def compute_interarrivals_ms(timestamps_s: list[float]) -> np.ndarray:
    timestamps = np.array(timestamps_s, dtype=np.float64)
    return np.diff(timestamps) * 1000.0


def format_stats(series: DensitySeries) -> str:
    mean_ms = float(np.mean(series.interarrivals_ms))
    std_ms = float(np.std(series.interarrivals_ms, ddof=1))
    return (
        f"{series.label}: count={len(series.interarrivals_ms)}, "
        f"mean={mean_ms:.9f} ms, std={std_ms:.9f} ms"
    )


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


def build_bin_edges(x_min: float, x_max: float, bin_width_ms: float) -> np.ndarray:
    if bin_width_ms <= 0:
        raise ValueError("bin width must be positive.")

    start = np.floor(x_min / bin_width_ms) * bin_width_ms
    stop = np.ceil(x_max / bin_width_ms) * bin_width_ms
    num_bins = max(1, int(np.ceil((stop - start) / bin_width_ms)))
    return np.linspace(start, start + num_bins * bin_width_ms, num_bins + 1)


def plot_densities(series_list: list[DensitySeries], output_path: Path, title: str) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(12, 4.8))

    stacked = np.concatenate([series.interarrivals_ms for series in series_list])
    x_min = float(np.min(stacked))
    x_max = float(np.max(stacked))
    x_pad = max(0.05, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)

    for series in series_list:
        values = series.interarrivals_ms
        mean_ms = float(np.mean(values))
        std_ms = float(np.std(values, ddof=1))
        label = f"{series.label} (mean={mean_ms:.2f}, std={std_ms:.2f})"
        bin_width_ms = COMPARISON_BIN_WIDTH_MS.get(series.key)
        if bin_width_ms is None:
            raise ValueError(f"Missing comparison bin width for series '{series.key}'.")
        edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

        ax.hist(
            values,
            bins=edges,
            density=True,
            alpha=0.55,
            label=label,
            color=FILLS[series.key],
            edgecolor="white",
            linewidth=0.7,
        )
        ax.plot(
            grid,
            gaussian_kde(values, grid),
            color=COLORS[series.key],
            linewidth=1.5,
        )

    ax.set_title(title)
    ax.set_xlabel("[ms]")
    ax.set_ylabel("Density Function")
    ax.set_xlim(*x_range)
    ax.grid(True, alpha=0.45, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=True)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_single_density(
    series: DensitySeries,
    output_path: Path,
    title: str,
) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(10, 4.8))

    values = series.interarrivals_ms
    x_min = float(np.min(values))
    x_max = float(np.max(values))
    x_pad = max(0.05, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)
    bin_width_ms = INDIVIDUAL_BIN_WIDTH_MS.get(series.key)
    if bin_width_ms is None:
        raise ValueError(f"Missing individual bin width for series '{series.key}'.")
    edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

    mean_ms = float(np.mean(values))
    std_ms = float(np.std(values, ddof=1))
    label = f"{series.label} (mean={mean_ms:.2f}, std={std_ms:.2f})"

    ax.hist(
        values,
        bins=edges,
        density=True,
        alpha=0.55,
        label=label,
        color=FILLS[series.key],
        edgecolor="white",
        linewidth=0.7,
    )
    ax.plot(
        grid,
        gaussian_kde(values, grid),
        color=COLORS[series.key],
        linewidth=1.5,
    )

    ax.set_title(title)
    ax.set_xlabel("[ms]")
    ax.set_ylabel("Density Function")
    ax.set_xlim(*x_range)
    ax.grid(True, alpha=0.45, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=True)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_trimmed_zoom_density(
    series: DensitySeries,
    output_path: Path,
    title: str,
) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(10, 4.8))

    values = series.interarrivals_ms
    low = float(np.quantile(values, 0.01))
    high = float(np.quantile(values, 0.99))
    trimmed = values[(values >= low) & (values <= high)]
    if len(trimmed) < 2:
        raise ValueError(f"Not enough trimmed samples left for series '{series.key}'.")

    x_min = float(np.min(trimmed))
    x_max = float(np.max(trimmed))
    x_pad = max(0.01, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)
    bin_width_ms = ZOOMED_TRIMMED_BIN_WIDTH_MS.get(series.key)
    if bin_width_ms is None:
        raise ValueError(f"Missing trimmed zoom bin width for series '{series.key}'.")
    edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

    mean_ms = float(np.mean(trimmed))
    std_ms = float(np.std(trimmed, ddof=1))
    label = f"{series.label} trimmed 1%-99% (mean={mean_ms:.2f}, std={std_ms:.2f})"

    ax.hist(
        trimmed,
        bins=edges,
        density=True,
        alpha=0.55,
        label=label,
        color=FILLS[series.key],
        edgecolor="white",
        linewidth=0.7,
    )
    ax.plot(
        grid,
        gaussian_kde(trimmed, grid),
        color=COLORS[series.key],
        linewidth=1.5,
    )

    ax.set_title(title)
    ax.set_xlabel("[ms]")
    ax.set_ylabel("Density Function")
    ax.set_xlim(*x_range)
    ax.grid(True, alpha=0.45, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=True)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def create_unified_grid(image_paths: list[tuple[str, Path]], output_path: Path) -> None:
    if len(image_paths) != 8:
        raise ValueError(f"Expected 8 images for the unified grid, got {len(image_paths)}.")

    fig = plt.figure(figsize=UNIFIED_GRID_FIGSIZE, constrained_layout=True)
    outer = fig.add_gridspec(4, 1, height_ratios=[1.0, 1.0, 1.0, 1.0], hspace=0.08)

    row1 = outer[0].subgridspec(1, 3, width_ratios=[1.0, 2.5, 1.0], wspace=0.0)
    row2 = outer[1].subgridspec(1, 3, wspace=0.06)
    row3 = outer[2].subgridspec(1, 2, wspace=0.06)
    row4 = outer[3].subgridspec(1, 2, wspace=0.06)

    axes = [
        fig.add_subplot(row1[0, 1]),
        fig.add_subplot(row2[0, 0]),
        fig.add_subplot(row2[0, 1]),
        fig.add_subplot(row2[0, 2]),
        fig.add_subplot(row3[0, 0]),
        fig.add_subplot(row3[0, 1]),
        fig.add_subplot(row4[0, 0]),
        fig.add_subplot(row4[0, 1]),
    ]

    for ax, (title, image_path) in zip(axes, image_paths):
        image = plt.imread(image_path)
        ax.imshow(image)
        ax.set_title(title, fontsize=12)
        ax.axis("off")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=UNIFIED_GRID_DPI)
    plt.close(fig)


def load_series(series_specs: list[str]) -> list[DensitySeries]:
    series_list: list[DensitySeries] = []
    for spec in series_specs:
        key, label, pcap_path = parse_series_spec(spec)
        if key not in COLORS:
            raise ValueError(f"Missing color for series '{key}'.")

        timestamps_s = load_timestamps_from_pcap(pcap_path)
        series_list.append(
            DensitySeries(
                key=key,
                label=label,
                pcap_path=pcap_path,
                interarrivals_ms=compute_interarrivals_ms(timestamps_s),
            )
        )

    return series_list


def main() -> int:
    args = parse_args()

    try:
        series_list = load_series(args.series)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    series_by_key = {series.key: series for series in series_list}

    for series in series_list:
        print(format_stats(series))

    plot_densities(series_list, args.output, args.title)
    print(f"Saved plot to {args.output}")

    generated_images: list[tuple[str, Path]] = [
        ("Comparison", args.output),
    ]

    for series in series_list:
        individual_output = args.individual_output_dir / f"{series.key}-interarrival-density.png"
        plot_single_density(
            series,
            individual_output,
            f"{series.label} Inter-arrival Density",
        )
        print(f"Saved plot to {individual_output}")
        generated_images.append((f"{series.label} Individual", individual_output))

    for key in args.zoomed:
        series = series_by_key.get(key)
        if series is None:
            print(f"Error: --zoomed key '{key}' does not match any --series key.", file=sys.stderr)
            return 1
        try:
            zoomed_output = args.zoomed_output_dir / f"{series.key}-interarrival-density-trimmed.png"
            plot_trimmed_zoom_density(
                series,
                zoomed_output,
                f"{series.label} Inter-arrival Density (1%-99% Trimmed)",
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Saved plot to {zoomed_output}")
        generated_images.append((f"{series.label} Zoomed Trimmed", zoomed_output))

    create_unified_grid(generated_images, args.unified_output)
    print(f"Saved plot to {args.unified_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
