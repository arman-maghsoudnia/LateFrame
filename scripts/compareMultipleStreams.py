#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_PCAPS = [
	REPO_ROOT / "comparison-data/Multiple_streams/ego4d/eval.pcap",
	REPO_ROOT / "comparison-data/Multiple_streams/google_meet/r_trace_eval_2025_08_27.pcap",
	REPO_ROOT / "comparison-data/Multiple_streams/swisscat/trace_7_eval.pcap",
	REPO_ROOT / "comparison-data/Multiple_streams/viratdata/eval.pcap",
	REPO_ROOT / "comparison-data/Multiple_streams/whatsapp/8_eval_PCAPdroid_21_Aug_17_18_03_filtered.pcap",
]


def parse_pcap(input_file: Path, display_filter: str | None = None) -> list[float]:
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
	if display_filter:
		cmd[1:1] = ["-Y", display_filter]

	def run_tshark(command: list[str]) -> subprocess.CompletedProcess[str]:
		return subprocess.run(command, check=True, capture_output=True, text=True)

	try:
		result = run_tshark(cmd)
	except FileNotFoundError as exc:
		raise RuntimeError("tshark is required but was not found in PATH.") from exc
	except subprocess.CalledProcessError as exc:
		stderr = exc.stderr.strip()
		if "Permission denied" in stderr or "don't have permission" in stderr:
			try:
				result = run_tshark(["sudo", "-n", *cmd])
			except FileNotFoundError as sudo_exc:
				raise RuntimeError("sudo is required but was not found in PATH.") from sudo_exc
			except subprocess.CalledProcessError as sudo_exc:
				raise RuntimeError(f"tshark failed for {pcap_path}:\n{sudo_exc.stderr.strip()}") from sudo_exc
		else:
			raise RuntimeError(f"tshark failed for {pcap_path}:\n{stderr}") from exc

	timestamps = [float(line) for line in result.stdout.splitlines() if line.strip()]
	if len(timestamps) < 2:
		raise ValueError(f"{pcap_path} does not contain enough packets to compute inter-arrival times")

	return (np.diff(np.asarray(timestamps, dtype=np.float64)) * 1000.0).tolist()


def compare_pcap_files(baseline: list[float], replay: list[float], title: str, output_file: Path) -> None:
	if len(baseline) != len(replay):
		min_len = min(len(baseline), len(replay))
		baseline = baseline[:min_len]
		replay = replay[:min_len]

	baseline_arr = np.asarray(baseline, dtype=np.float64)
	replay_arr = np.asarray(replay, dtype=np.float64)
	mae = float(np.mean(np.abs(baseline_arr - replay_arr)))

	print(f"{title} - MAE: {mae:.6f} ms")

	plt.figure(figsize=(10, 6))
	plt.plot(np.abs(baseline_arr - replay_arr), label="Error", alpha=0.7)
	plt.xlabel("Packet Index")
	plt.ylabel("Absolute Inter-arrival Time Difference [ms]")
	plt.title(f"Absolute Inter-arrival Time Differences: {title}")
	plt.legend()
	plt.grid()
	plt.tight_layout()
	plt.savefig(output_file)
	plt.close()


def main() -> None:
	capture_file = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "comparison-data/Multiple_streams/results.pcap"
	if not capture_file.is_absolute():
		capture_file = REPO_ROOT / capture_file
	count = int(sys.argv[2]) if len(sys.argv) > 2 else 10

	output_dir = REPO_ROOT / "docs/Multiple_streams"
	output_dir.mkdir(parents=True, exist_ok=True)

	for i in range(count):
		source_pcap = SOURCE_PCAPS[i % len(SOURCE_PCAPS)]
		port = 12301 + i
		title = f"Stream {i + 1} (port {port})"
		replay_filter = f"tcp.dstport == {port} || udp.dstport == {port}"

		source_data = parse_pcap(source_pcap)
		replay_data = parse_pcap(capture_file, replay_filter)
		output_file = output_dir / f"comparison_stream_{i + 1}.png"

		compare_pcap_files(source_data, replay_data, title, output_file)


if __name__ == "__main__":
	main()
