# LateFrame

LateFrame is a Linux UDP traffic generator for experiments where packet inter-arrival time matters.

The motivation is simple: `ping` and `fping` are useful network tools, but they are not precise packet schedulers. If you need to generate traffic at a target interval with low jitter, they are the wrong baseline.

LateFrame uses absolute deadline scheduling on `CLOCK_MONOTONIC`, CPU pinning, and a minimal send loop to keep pacing tight. By default it uses `timerfd`, and it can also use `clock_nanosleep()` with an optional busy-spin window.

## Modes

LateFrame supports two main use cases:

- synthetic traffic generation with controlled inter-arrival timing
- replay of previously captured packet traces from PCAP files through UDP encapsulation

Synthetic generation is useful when you want a clean constant, Poisson, or Gaussian process.

PCAP replay is useful when you need to reproduce a captured trace while preserving the original packet timing. LateFrame retains as much as possible from the end of each captured packet inside a UDP payload, so the replayed packet matches the captured packet length whenever possible after accounting for the replay encapsulation overhead. This matters in replay scenarios where sending packets with the same size and timing is more important than reconstructing the exact original packet contents.

This is especially useful for experiments and research where accurate workload replay in terms of packet size and timing matters more than generating identical packets.

If the original packet is smaller than the required replay encapsulation overhead, the replayed packet cannot match the original packet size and will be larger. In this case, the user is warned.

## Result

The current comparison uses a `100 ms` target interval and `1000` transmitted packets. The figure below includes:

- the combined comparison
- one standalone plot for each sender
- trimmed zoomed plots for `ping` and `fping` with the top and bottom 1% removed

LateFrame: count=999, mean=99.999980526 ms, std=0.053337378 ms  
ping: count=999, mean=104.012259731 ms, std=0.517701949 ms  
fping: count=999, mean=100.000159280 ms, std=0.247248914 ms

![Inter-arrival results grid](docs/interarrival-density-results-grid.png)

Two things stand out in the captures:

- `ping` misses the target mean by about `4 ms`.
- `ping` and `fping` both show substantial outliers, including deviations greater than `4 ms`, while LateFrame stays much tighter.
- LateFrame has no outlier and its distribution is narrower around the target interarrival time. 
- LateFrame's interarrival mean is 19.47us off from the target interarrival while for fping and ping this number is 159.28us and 4.01ms respectively. 

## Build

Dependencies on Debian or Ubuntu:

```bash
sudo apt install build-essential make libpcap-dev tshark
```

Build:

```bash
git clone https://github.com/arman-maghsoudnia/LateFrame.git
cd LateFrame
make
```

The binary is produced at `package/usr/bin/lateframe`.

Install:

```bash
sudo make install
```

Or:

```bash
make install PREFIX=/opt/lateframe
```

## Usage

```bash
lateframe [options]
```

Main options:

- `-n`, `--num-packets`: number of packets to send
- `-i`, `--interface`: source interface
- `-d`, `--destination`: destination IPv4 address
- `-p`, `--port`: destination UDP port
- `-t`, `--distribution`: `constant`, `poisson`, `gaussian`, or `pcap`
- `-a`, `--param`: interval in ms, lambda in packets/s, or Gaussian mean in ms
- `-S`, `--sigma`: Gaussian sigma in ms
- `-s`, `--size`: payload size in bytes
- `-f`, `--pcap-file`: PCAP file for replay mode
- `--wait-mode`: `timerfd` or `nanosleep` for the packet pacing wait primitive
- `--spin-us`: busy-spin window in microseconds for `nanosleep` wait mode
- `-l`, `--log`: log sends to stdout and `/tmp/lateframe.log`
- `-c`, `--capture`: capture generated packets to `/tmp/lateframe-capture.pcap`

Notes:

- Option order does not matter.
- Both `--num-packets` and legacy `--num_packets` are accepted.
- `--wait-mode` defaults to `timerfd`.
- `--wait-mode nanosleep` requires `--spin-us`.
- Generated traffic modes require `-n`, `-s`, and `-a`.
- Gaussian mode also requires `-S`.
- PCAP mode ignores `-n`, `-s`, `-a`, and `-S`.

Examples:

```bash
sudo lateframe -n 1000 -i eth0 -d 192.168.1.10 -p 12345 -t constant -a 100 -s 256
```

```bash
sudo lateframe -n 1000 -i eth0 -d 192.168.1.10 -p 12345 -t poisson -a 100 -s 256
```

```bash
sudo lateframe -n 1000 -i eth0 -d 192.168.1.10 -p 12345 -t gaussian -a 40 -S 2 -s 256
```

```bash
sudo lateframe -i eth0 -d 192.168.1.10 -p 12345 -t pcap -f trace.pcap
```

```bash
sudo lateframe -i eth0 -d 192.168.1.10 -p 12345 -t pcap -f trace.pcap --wait-mode nanosleep --spin-us 100
```

## Reproducing The Comparison

The PCAPs used for the current result are in `comparison-data/`. They were captured with the commands below.

### fping

Send:

```bash
sudo fping -c 1000 -p 100 128.178.122.100
```

Capture on destination:

```bash
sudo tshark -i eno1 -f "icmp[0] = 8 and host 128.178.122.100" -w /tmp/fping-test.pcap
```

### ping

Send:

```bash
sudo ping 128.178.122.100 -i 0.1 -c 1000
```

Capture on destination:

```bash
sudo tshark -i eno1 -f "icmp[0] = 8 and host 128.178.122.100" -w /tmp/ping-test.pcap
```

### LateFrame

Send:

```bash
sudo lateframe -n 1000 -i eno1 -d 128.178.122.100 -p 12345 -t constant -a 100 -s 256 -c
```

Capture on destination:

```bash
sudo tshark -i eno1 -f "udp and host 128.178.122.100 and port 12345" -w /tmp/lateframe-out.pcap
```

Versions used:

- `ping`: `ping from iputils 20240117`
- `fping`: `Version 5.1`

Host used for the run:

- Architecture: `x86_64`
- CPU: `Intel(R) Xeon(R) W-2225 CPU @ 4.10GHz`
- Sockets: `1`
- Cores per socket: `4`
- Threads per core: `2`
- Logical CPUs: `8`
- CPU max frequency: `4600.0000 MHz`
- CPU min frequency: `1200.0000 MHz`
- L1d cache: `128 KiB (4 instances)`
- L1i cache: `128 KiB (4 instances)`
- L2 cache: `4 MiB (4 instances)`
- L3 cache: `8.3 MiB (1 instance)`
- NUMA nodes: `1`
- Virtualization: `VT-x`

## Plotting

To regenerate the figures from the PCAPs:

```bash
python3 scripts/plot_interarrival_density.py
```

This produces:

- `docs/interarrival-density-comparison.png`
- `docs/individual-density-plots/lateframe-interarrival-density.png`
- `docs/individual-density-plots/ping-interarrival-density.png`
- `docs/individual-density-plots/fping-interarrival-density.png`
- `docs/zoomed-density-plots/ping-interarrival-density-trimmed.png`
- `docs/zoomed-density-plots/fping-interarrival-density-trimmed.png`
- `docs/interarrival-density-results-grid.png`

## PCAP Replay

PCAP replay mode does not send raw frames. It reads a PCAP, preserves the observed inter-arrival timing, and encapsulates the captured bytes into UDP packets.

For a captured packet of length `X`, LateFrame preserves as many bytes as possible from the end of that packet and uses them as the UDP payload, so:

- if `X` is at least as large as the replay encapsulation overhead, the replayed packet has length `X`
- if `X` is smaller than the replay encapsulation overhead, the replayed packet must be larger than the original

This is not raw frame replay. It is timing-preserving UDP encapsulation of captured packet bytes.

For Ethernet captures, the replay encapsulation overhead is Ethernet + IPv4 + UDP headers. For raw IPv4 captures, it is IPv4 + UDP headers. LateFrame prints a warning when exact size matching is not possible.

Any captured packet bytes can be encapsulated this way. If a packet was truncated in the PCAP, replay uses the captured length, because the missing bytes are not available.

## How It Works

- Absolute `timerfd` scheduling on `CLOCK_MONOTONIC`
- CPU pinning
- Best-effort `SCHED_FIFO`
- Pre-built payloads for generated traffic
- Optional `tshark` capture during transmission

For generated traffic modes, LateFrame writes a sequence ID at the beginning of each UDP payload. That makes packet matching easier in captures and receiver logs.

## Output Files

- `/tmp/lateframe.log`
- `/tmp/lateframe-capture.pcap`
