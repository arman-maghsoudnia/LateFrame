# CPU Pinning Comparison

LateFrame pins the sending thread to a CPU by default. This comparison replays the same source trace twice:

- once with default CPU pinning enabled
- once with CPU pinning disabled through `--no-cpu-pin`

The source trace is:

```bash
comparison-data/CPU_pinning/original.pcap
```

## Results

Replay error against `comparison-data/CPU_pinning/original.pcap`:

- `Pinned CPU`: abs mean `0.015209288 ms`, std `0.030485516 ms`, min `-0.147581100 ms`, max `0.479221344 ms`
- `Unpinned CPU`: abs mean `0.015434510 ms`, std `0.031531896 ms`, min `-0.147819519 ms`, max `0.478029251 ms`

![CPU pinning replay inter-arrival heartbeat aggregate](../../docs/CPU_pinning/cpu-pinning-interarrival-diff-heartbeat-aggregate.png)

![CPU pinning replay inter-arrival density aggregate](../../docs/CPU_pinning/cpu-pinning-interarrival-diff-density-aggregate.png)

Host used for the run:

- Architecture: `x86_64`
- CPU: `Intel(R) Xeon(R) W-2225 CPU @ 4.10GHz`
- Vendor: `GenuineIntel`
- Sockets: `1`
- Cores per socket: `4`
- Threads per core: `2`
- Logical CPUs: `8`
- CPU max frequency: `4100.0000 MHz`
- CPU min frequency: `1200.0000 MHz`
- Kernel: `6.8.0-117-lowlatency`
- CPU governor: `performance`
- L1d cache: `128 KiB (4 instances)`
- L1i cache: `128 KiB (4 instances)`
- L2 cache: `4 MiB (4 instances)`
- L3 cache: `8.3 MiB (1 instance)`
- NUMA nodes: `1`
- Virtualization: `VT-x`

## Regenerating The Plots

From the repository root:

```bash
python3 scripts/plot_interarrival_diff.py \
  --original comparison-data/CPU_pinning/original.pcap \
  --replay "replay_pin=Pinned CPU=comparison-data/CPU_pinning/replay_pin.pcap" \
  --replay "replay_NOpin=Unpinned CPU=comparison-data/CPU_pinning/replay_NOpin.pcap" \
  --output-dir docs/CPU_pinning \
  --output-prefix cpu-pinning
```

This produces heartbeat replay-difference plots and density replay-difference plots under `docs/CPU_pinning/`.

## Reproducing The Captures

### CPU pinning enabled

Capture on the destination:

```bash
sudo tshark -i eno1 -f "udp port 12345" -w /tmp/replay_pin.pcap
```

Send:

```bash
sudo lateframe -i enp113s0 -d 128.178.122.100 -p 12345 -t pcap -f comparison-data/CPU_pinning/original.pcap --wait-mode timerfd
```

### CPU pinning disabled

Capture on the destination:

```bash
sudo tshark -i eno1 -f "udp port 12345" -w /tmp/replay_NOpin.pcap
```

Send:

```bash
sudo lateframe -i enp113s0 -d 128.178.122.100 -p 12345 -t pcap -f comparison-data/CPU_pinning/original.pcap --wait-mode timerfd --no-cpu-pin
```

Then copy the replay PCAPs into `comparison-data/CPU_pinning/` and regenerate the plots.
