#!/usr/bin/env bash

set -Eeuo pipefail

count=${1:-5}

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pcaps=(
    "$base_dir/ego4d/eval.pcap"
    "$base_dir/google_meet/r_trace_eval_2025_08_27.pcap"
    "$base_dir/swisscat/trace_7_eval.pcap"
    "$base_dir/viratdata/eval.pcap"
    "$base_dir/whatsapp/8_eval_PCAPdroid_21_Aug_17_18_03_filtered.pcap"
)

capture_file="$base_dir/tshark-multi-streams.pcap"
capture_filter=""

for ((i = 0; i < count; i++)); do
    port=$((12301 + i))
    if [[ -n "$capture_filter" ]]; then
        capture_filter+=" or "
    fi
    capture_filter+="dst port $port"
done

# Remove old capture to avoid confusion
rm -f "$capture_file"

echo "Capture file: $capture_file"

# Pre-authenticate sudo once
sudo -v

# Keep sudo alive in background
while true; do
    sudo -n true
    sleep 60
    kill -0 "$$" || exit
done 2>/dev/null &
sudo_keeper_pid=$!

lateframe_pids=()
cleanup_done=0

cleanup() {
    [[ $cleanup_done -eq 1 ]] && return
    cleanup_done=1

    echo
    echo "Cleaning up..."

    # Stop generators first
    for pid in "${lateframe_pids[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
            sudo kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait for generators
    for pid in "${lateframe_pids[@]:-}"; do
        wait "$pid" 2>/dev/null || true
    done

    # Stop tshark last so final packets are flushed
    if [[ -n "${tshark_pid:-}" ]] && kill -0 "$tshark_pid" 2>/dev/null; then
        sudo kill -TERM "$tshark_pid" 2>/dev/null || true
        wait "$tshark_pid" 2>/dev/null || true
    fi

    # Stop sudo keepalive
    kill "$sudo_keeper_pid" 2>/dev/null || true

    # Verify capture exists
    if [[ -f "$capture_file" ]]; then
        size=$(stat -c%s "$capture_file" 2>/dev/null || echo 0)
        echo "Capture saved ($size bytes)"
    else
        echo "Capture file missing"
    fi
}

trap cleanup EXIT INT TERM

# Start capture
# sudo tshark -i lo -f "$capture_filter" -w "$capture_file" >/dev/null 2>&1 &
sudo tshark -i lo -f "$capture_filter" -w /tmp/test.pcap >/dev/null 2>&1 &
tshark_pid=$!

echo "Started tshark using filter: $capture_filter"

sleep 1

# Verify tshark actually started
if ! kill -0 "$tshark_pid" 2>/dev/null; then
    echo "Failed to start tshark"
    exit 1
fi

echo "Started tshark (PID $tshark_pid)"

# Start streams
for ((i = 0; i < count; i++)); do
    pcap="${pcaps[$((i % ${#pcaps[@]}))]}"
    port=$((12301 + i))

    echo "Starting stream $i on port $port"

    sudo lateframe \
        -i lo \
        -d 127.0.0.1 \
        -p "$port" \
        -t pcap \
        -f "$pcap" \
        --no-cpu-pin \
        >/dev/null 2>&1 &

    lateframe_pids+=("$!")
done

# Wait only for generators
for pid in "${lateframe_pids[@]}"; do
    wait "$pid"
done

echo "All streams completed"