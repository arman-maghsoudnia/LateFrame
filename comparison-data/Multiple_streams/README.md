# Multiple Streams
This directory contains multiple pcap files for testing LateFrame's performance with multiple concurrent streams. 

## Run the tests
1. Move to [this directory](./):
    ```bash
    cd comparison-data/Multiple_streams 
    ```
2. Run the bash script to replay the pcap files with LateFrame. The script takes one argument, which is the number of concurrent streams to replay (e.g., `N=4` for 4 concurrent streams):
    ```bash
    ./run.sh $N
    ```
    The PCAP files used in this test are too big to be stored in this repository, please contact the maintainers to obtain the files. This test can also be run with custom pcap files by modifying the `run.sh` script to point to the desired pcap files. The script will replay the packets in the pcap files using LateFrame and capture the results for analysis.
3. The `pcap` file will be stored in [this directory](./). <br>
To differentiate the individual streams, LateFrame uses the source port number. The source port numbers for the streams are as follows:
    - Stream 1: Source port 12301
    - Stream 2: Source port 12302
    - Stream N: Source port 12300 + N


## Analyze the results
Run the [Python script](/scripts/compareMultipleStreams.py) to analyze the captured pcap file and generate latency graphs.
