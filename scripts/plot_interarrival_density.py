#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


DEFAULT_INPUT_DIR = Path("comparison-data")
DEFAULT_OUTPUT = Path("docs/interarrival-density-comparison.png")
DEFAULT_INDIVIDUAL_OUTPUT_DIR = Path("docs/individual-density-plots")
DEFAULT_ZOOMED_OUTPUT_DIR = Path("docs/zoomed-density-plots")
DEFAULT_UNIFIED_OUTPUT = Path("docs/interarrival-density-results-grid.png")
DEFAULT_PCAPS = {
    "LateFrame": "lateframe-out.pcap",
    "ping": "ping-test.pcap",
    "fping": "fping-test.pcap",
}

# Hardcode the histogram bin width per series for the combined comparison plot.
COMPARISON_BIN_WIDTH_MS = {
    "LateFrame": 0.05,
    "ping": 0.05,
    "fping": 0.05,
}

# Hardcode the histogram bin width per series for the individual plots.
INDIVIDUAL_BIN_WIDTH_MS = {
    "LateFrame": 0.005,
    "ping": 0.05,
    "fping": 0.02,
}

ZOOMED_TRIMMED_BIN_WIDTH_MS = {
    "ping": 0.02,
    "fping": 0.01,
}

UNIFIED_GRID_DPI = 600
UNIFIED_GRID_FIGSIZE = (20, 7.2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot density histograms of packet inter-arrival times from comparison PCAP files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing the comparison PCAP files (default: {DEFAULT_INPUT_DIR}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Path to save the output figure (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--individual-output-dir",
        type=Path,
        default=DEFAULT_INDIVIDUAL_OUTPUT_DIR,
        help=(
            "Directory to save one figure per series "
            f"(default: {DEFAULT_INDIVIDUAL_OUTPUT_DIR})."
        ),
    )
    parser.add_argument(
        "--zoomed-output-dir",
        type=Path,
        default=DEFAULT_ZOOMED_OUTPUT_DIR,
        help=(
            "Directory to save zoomed ping/fping plots with the top and bottom "
            f"1 percent removed (default: {DEFAULT_ZOOMED_OUTPUT_DIR})."
        ),
    )
    parser.add_argument(
        "--unified-output",
        type=Path,
        default=DEFAULT_UNIFIED_OUTPUT,
        help=f"Path to save the unified 6-panel PNG (default: {DEFAULT_UNIFIED_OUTPUT}).",
    )
    parser.add_argument(
        "--title",
        default="Inter-arrival Density Comparison",
        help="Plot title.",
    )
    return parser.parse_args()


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


def format_stats(name: str, interarrivals_ms: np.ndarray) -> str:
    mean_ms = float(np.mean(interarrivals_ms))
    std_ms = float(np.std(interarrivals_ms, ddof=1))
    return (
        f"{name}: count={len(interarrivals_ms)}, "
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


def plot_densities(series: dict[str, np.ndarray], output_path: Path, title: str) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(12, 4.8))

    colors = {
        "LateFrame": "#12a8f0",
        "ping": "#86bf10",
        "fping": "#ff5a1f",
    }
    fills = {
        "LateFrame": "#7ccbf3",
        "ping": "#b8dd67",
        "fping": "#f7a287",
    }

    stacked = np.concatenate(list(series.values()))
    x_min = float(np.min(stacked))
    x_max = float(np.max(stacked))
    x_pad = max(0.05, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)

    for name, values in series.items():
        mean_ms = float(np.mean(values))
        std_ms = float(np.std(values, ddof=1))
        label = f"{name} (mean={mean_ms:.2f}, std={std_ms:.2f})"
        bin_width_ms = COMPARISON_BIN_WIDTH_MS.get(name)
        if bin_width_ms is None:
            raise ValueError(f"Missing comparison bin width for series '{name}'.")
        edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

        ax.hist(
            values,
            bins=edges,
            density=True,
            alpha=0.55,
            label=label,
            color=fills[name],
            edgecolor="white",
            linewidth=0.7,
        )
        ax.plot(
            grid,
            gaussian_kde(values, grid),
            color=colors[name],
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
    name: str,
    values: np.ndarray,
    output_path: Path,
    title: str,
) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(10, 4.8))

    colors = {
        "LateFrame": "#12a8f0",
        "ping": "#86bf10",
        "fping": "#ff5a1f",
    }
    fills = {
        "LateFrame": "#7ccbf3",
        "ping": "#b8dd67",
        "fping": "#f7a287",
    }

    x_min = float(np.min(values))
    x_max = float(np.max(values))
    x_pad = max(0.05, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)
    bin_width_ms = INDIVIDUAL_BIN_WIDTH_MS.get(name)
    if bin_width_ms is None:
        raise ValueError(f"Missing individual bin width for series '{name}'.")
    edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

    mean_ms = float(np.mean(values))
    std_ms = float(np.std(values, ddof=1))
    label = f"{name} (mean={mean_ms:.2f}, std={std_ms:.2f})"

    ax.hist(
        values,
        bins=edges,
        density=True,
        alpha=0.55,
        label=label,
        color=fills[name],
        edgecolor="white",
        linewidth=0.7,
    )
    ax.plot(
        grid,
        gaussian_kde(values, grid),
        color=colors[name],
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
    name: str,
    values: np.ndarray,
    output_path: Path,
    title: str,
) -> None:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(10, 4.8))

    colors = {
        "ping": "#86bf10",
        "fping": "#ff5a1f",
    }
    fills = {
        "ping": "#b8dd67",
        "fping": "#f7a287",
    }

    low = float(np.quantile(values, 0.01))
    high = float(np.quantile(values, 0.99))
    trimmed = values[(values >= low) & (values <= high)]
    if len(trimmed) < 2:
        raise ValueError(f"Not enough trimmed samples left for series '{name}'.")

    x_min = float(np.min(trimmed))
    x_max = float(np.max(trimmed))
    x_pad = max(0.01, (x_max - x_min) * 0.08)
    x_range = (x_min - x_pad, x_max + x_pad)
    grid = np.linspace(x_range[0], x_range[1], 1200)
    bin_width_ms = ZOOMED_TRIMMED_BIN_WIDTH_MS.get(name)
    if bin_width_ms is None:
        raise ValueError(f"Missing trimmed zoom bin width for series '{name}'.")
    edges = build_bin_edges(x_range[0], x_range[1], bin_width_ms)

    mean_ms = float(np.mean(trimmed))
    std_ms = float(np.std(trimmed, ddof=1))
    label = f"{name} trimmed 1%-99% (mean={mean_ms:.2f}, std={std_ms:.2f})"

    ax.hist(
        trimmed,
        bins=edges,
        density=True,
        alpha=0.55,
        label=label,
        color=fills[name],
        edgecolor="white",
        linewidth=0.7,
    )
    ax.plot(
        grid,
        gaussian_kde(trimmed, grid),
        color=colors[name],
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
    fig, axes = plt.subplots(2, 3, figsize=UNIFIED_GRID_FIGSIZE)

    for ax, (title, image_path) in zip(axes.flat, image_paths):
        image = plt.imread(image_path)
        ax.imshow(image)
        ax.set_title(title, fontsize=12)
        ax.axis("off")

    fig.tight_layout(pad=0.4, w_pad=0.4, h_pad=0.5)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=UNIFIED_GRID_DPI)
    plt.close(fig)


def main() -> int:
    args = parse_args()

    pcap_paths = {
        name: args.input_dir / filename
        for name, filename in DEFAULT_PCAPS.items()
    }

    for path in pcap_paths.values():
        if not path.exists():
            print(f"Error: missing input file: {path}", file=sys.stderr)
            return 1

    series: dict[str, np.ndarray] = {}
    try:
        for name, path in pcap_paths.items():
            timestamps_s = load_timestamps_from_pcap(path)
            series[name] = compute_interarrivals_ms(timestamps_s)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for name, values in series.items():
        print(format_stats(name, values))

    plot_densities(series, args.output, args.title)
    print(f"Saved plot to {args.output}")

    generated_images: list[tuple[str, Path]] = [
        ("Comparison", args.output),
    ]

    for name, values in series.items():
        individual_output = args.individual_output_dir / f"{name.lower()}-interarrival-density.png"
        plot_single_density(
            name,
            values,
            individual_output,
            f"{name} Inter-arrival Density",
        )
        print(f"Saved plot to {individual_output}")
        generated_images.append((f"{name} Individual", individual_output))

    for name in ("ping", "fping"):
        zoomed_output = args.zoomed_output_dir / f"{name.lower()}-interarrival-density-trimmed.png"
        plot_trimmed_zoom_density(
            name,
            series[name],
            zoomed_output,
            f"{name} Inter-arrival Density (1%-99% Trimmed)",
        )
        print(f"Saved plot to {zoomed_output}")
        generated_images.append((f"{name} Zoomed Trimmed", zoomed_output))

    create_unified_grid(generated_images, args.unified_output)
    print(f"Saved plot to {args.unified_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
