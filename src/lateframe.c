#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <math.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <netinet/ip.h>
#include <netinet/udp.h>
#include <pcap.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/timerfd.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define PROGRAM_NAME "lateframe"
#ifndef LATEFRAME_VERSION
#define LATEFRAME_VERSION "0.0.0-dev"
#endif
#define LOG_PATH "/tmp/lateframe.log"
#define CAPTURE_PATH "/tmp/lateframe-capture.pcap"

typedef enum {
    MODE_UNSET = 0,
    MODE_CONSTANT,
    MODE_POISSON,
    MODE_GAUSSIAN,
    MODE_PCAP
} traffic_mode_t;

typedef enum {
    WAIT_MODE_TIMERFD = 0,
    WAIT_MODE_NANOSLEEP
} wait_mode_t;

typedef struct {
    int64_t inter_arrival_ns;
    size_t packet_size;
    u_char *packet_data;
} replay_packet_t;

static pid_t tshark_pid = -1;
/* Global flag: CPU pinning enabled by default. Set to 0 to disable. */
static int cpu_pinning_enabled = 1;

static void warn_errno(const char *message) {
    fprintf(stderr, "Warning: %s: %s\n", message, strerror(errno));
}

static void set_realtime_priority(int priority) {
    struct sched_param param;

    memset(&param, 0, sizeof(param));
    param.sched_priority = priority;

    if (sched_setscheduler(0, SCHED_FIFO, &param) == -1) {
        warn_errno("failed to set real-time priority, continuing without it");
    }
}

static void set_cpu_affinity(int cpu_index) {
    cpu_set_t cpuset;

    CPU_ZERO(&cpuset);
    CPU_SET(cpu_index, &cpuset);

    if (sched_setaffinity(0, sizeof(cpuset), &cpuset) == -1) {
        warn_errno("failed to set CPU affinity");
    }
}

static void set_non_blocking(int sock) {
    int flags = fcntl(sock, F_GETFL, 0);

    if (flags < 0) {
        perror("fcntl(F_GETFL) failed");
        exit(1);
    }

    if (fcntl(sock, F_SETFL, flags | O_NONBLOCK) < 0) {
        perror("fcntl(F_SETFL) failed");
        exit(1);
    }
}

static double uniform_exclusive(void) {
    return (rand() + 1.0) / ((double)RAND_MAX + 2.0);
}

static double generate_exponential(double lambda) {
    return -log(uniform_exclusive()) / lambda;
}

static int64_t generate_gaussian_ns(double mean_ms, double sigma_ms) {
    double mean_ns = mean_ms * 1e6;
    double sigma_ns = sigma_ms * 1e6;
    double u1 = uniform_exclusive();
    double u2 = uniform_exclusive();
    double z = sqrt(-2.0 * log(u1)) * cos(2.0 * M_PI * u2);
    double interval_ns = mean_ns + (sigma_ns * z);

    if (interval_ns < 0.0) {
        return 0;
    }

    if (interval_ns > (double)INT64_MAX) {
        return INT64_MAX;
    }

    return (int64_t)llround(interval_ns);
}

static void print_help(void) {
    printf("Usage: %s [options]\n", PROGRAM_NAME);
    printf("Version: %s\n", LATEFRAME_VERSION);
    printf("Options:\n");
    printf("  -n, --num-packets NUM      Number of packets to send\n");
    printf("      --num_packets NUM      Backward-compatible alias\n");
    printf("  -i, --interface IFACE      Source interface (for example, eth0)\n");
    printf("  -d, --destination IP       Destination IPv4 address\n");
    printf("  -p, --port PORT            Destination UDP port\n");
    printf("  -t, --distribution TYPE    constant, poisson, gaussian, or pcap\n");
    printf("  -a, --param VALUE          Interval in ms, lambda in packets/s, or Gaussian mean in ms\n");
    printf("  -S, --sigma VALUE          Gaussian sigma in ms\n");
    printf("  -s, --size BYTES           Payload size for generated traffic\n");
    printf("  -f, --pcap-file PATH       PCAP file to replay in pcap mode\n");
    printf("      --pcap_file PATH       Backward-compatible alias\n");
    printf("      --wait-mode MODE       timerfd (default) or nanosleep\n");
    printf("      --spin-us USEC         Busy-spin for the last USEC before each deadline in nanosleep mode\n");
    printf("      --sping-us USEC        Backward-compatible alias for --spin-us\n");
    printf("      --no-cpu-pin            Disable CPU pinning (default: enabled)\n");
    printf("  -l, --log                  Log packet sends to stdout and %s\n", LOG_PATH);
    printf("  -c, --capture              Capture outgoing packets with tshark to %s\n", CAPTURE_PATH);
    printf("  -V, --version              Display version information\n");
    printf("  -h, --help                 Display this help message\n");
}

static void print_version(void) {
    printf("%s %s\n", PROGRAM_NAME, LATEFRAME_VERSION);
}

static int parse_positive_int(const char *text, const char *flag_name, int *out) {
    char *end = NULL;
    long value = 0;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        fprintf(stderr, "Error: %s expects an integer, got '%s'.\n", flag_name, text);
        return -1;
    }
    if (value <= 0 || value > INT32_MAX) {
        fprintf(stderr, "Error: %s must be in the range 1..%d.\n", flag_name, INT32_MAX);
        return -1;
    }

    *out = (int)value;
    return 0;
}

static int parse_non_negative_double(const char *text, const char *flag_name, double *out) {
    char *end = NULL;
    double value = 0.0;

    errno = 0;
    value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0') {
        fprintf(stderr, "Error: %s expects a number, got '%s'.\n", flag_name, text);
        return -1;
    }
    if (value < 0.0) {
        fprintf(stderr, "Error: %s must be non-negative.\n", flag_name);
        return -1;
    }

    *out = value;
    return 0;
}

static int parse_non_negative_int(const char *text, const char *flag_name, int *out) {
    char *end = NULL;
    long value = 0;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        fprintf(stderr, "Error: %s expects an integer, got '%s'.\n", flag_name, text);
        return -1;
    }
    if (value < 0 || value > INT32_MAX) {
        fprintf(stderr, "Error: %s must be in the range 0..%d.\n", flag_name, INT32_MAX);
        return -1;
    }

    *out = (int)value;
    return 0;
}

static traffic_mode_t parse_mode(const char *value) {
    if (strcasecmp(value, "constant") == 0) {
        return MODE_CONSTANT;
    }
    if (strcasecmp(value, "poisson") == 0) {
        return MODE_POISSON;
    }
    if (strcasecmp(value, "gaussian") == 0) {
        return MODE_GAUSSIAN;
    }
    if (strcasecmp(value, "pcap") == 0) {
        return MODE_PCAP;
    }

    return MODE_UNSET;
}

static wait_mode_t parse_wait_mode(const char *value) {
    if (strcasecmp(value, "timerfd") == 0) {
        return WAIT_MODE_TIMERFD;
    }
    if (strcasecmp(value, "nanosleep") == 0) {
        return WAIT_MODE_NANOSLEEP;
    }

    return -1;
}

static void start_packet_capture(const char *source_interface, const char *dest_ip) {
    if (system("command -v tshark > /dev/null 2>&1") != 0) {
        fprintf(stderr, "Error: tshark is not installed or not in PATH.\n");
        exit(1);
    }

    tshark_pid = fork();
    if (tshark_pid < 0) {
        perror("Failed to create tshark process");
        exit(1);
    }

    if (tshark_pid == 0) {
        char filter[128];
        int devnull = -1;
        int ret = 0;

        // set_cpu_affinity(1);
        set_realtime_priority(99);

        ret = snprintf(filter, sizeof(filter), "udp and ip dst %s", dest_ip);
        if (ret < 0 || ret >= (int)sizeof(filter)) {
            fprintf(stderr, "Error: tshark filter is too long.\n");
            exit(1);
        }

        devnull = open("/dev/null", O_WRONLY);
        if (devnull == -1) {
            perror("Failed to open /dev/null");
            exit(1);
        }

        if (dup2(devnull, STDOUT_FILENO) == -1 || dup2(devnull, STDERR_FILENO) == -1) {
            perror("Failed to redirect tshark output");
            exit(1);
        }
        close(devnull);

        execlp(
            "tshark",
            "tshark",
            "-i",
            source_interface,
            "-f",
            filter,
            "-w",
            CAPTURE_PATH,
            (char *)NULL
        );

        perror("Failed to start tshark");
        exit(1);
    }

    usleep(200000);
}

static void stop_packet_capture(void) {
    if (tshark_pid > 0) {
        usleep(200000);
        kill(tshark_pid, SIGINT);
        waitpid(tshark_pid, NULL, 0);
    }
}

static int create_timerfd(void) {
    int tfd = timerfd_create(CLOCK_MONOTONIC, 0);

    if (tfd < 0) {
        perror("timerfd_create failed");
        return -1;
    }

    return tfd;
}

static void set_timer_absolute(int fd, const struct timespec *abs_time) {
    struct itimerspec its;

    memset(&its, 0, sizeof(its));
    its.it_value = *abs_time;

    if (timerfd_settime(fd, TFD_TIMER_ABSTIME, &its, NULL) == -1) {
        perror("timerfd_settime (absolute) failed");
        exit(1);
    }
}

static void wait_for_next_tick(int fd) {
    uint64_t expirations = 0;
    ssize_t bytes_read = read(fd, &expirations, sizeof(expirations));

    if (bytes_read != (ssize_t)sizeof(expirations)) {
        perror("Error reading from timerfd");
        exit(1);
    }
}

static void add_ns(struct timespec *ts, int64_t ns) {
    if (ns < 0) {
        ns = 0;
    }

    ts->tv_sec += ns / 1000000000LL;
    ts->tv_nsec += ns % 1000000000LL;

    while (ts->tv_nsec >= 1000000000L) {
        ts->tv_nsec -= 1000000000L;
        ts->tv_sec++;
    }
}

static int compare_timespec(const struct timespec *a, const struct timespec *b) {
    if (a->tv_sec != b->tv_sec) {
        return (a->tv_sec < b->tv_sec) ? -1 : 1;
    }
    if (a->tv_nsec != b->tv_nsec) {
        return (a->tv_nsec < b->tv_nsec) ? -1 : 1;
    }

    return 0;
}

static void subtract_ns(struct timespec *ts, int64_t ns) {
    if (ns <= 0) {
        return;
    }

    ts->tv_sec -= ns / 1000000000LL;
    ts->tv_nsec -= ns % 1000000000LL;

    while (ts->tv_nsec < 0) {
        ts->tv_nsec += 1000000000L;
        ts->tv_sec--;
    }
}

static void wait_until_deadline(const struct timespec *deadline, int64_t spin_duration_ns) {
    struct timespec sleep_deadline;
    struct timespec now;

    sleep_deadline = *deadline;
    if (spin_duration_ns > 0) {
        subtract_ns(&sleep_deadline, spin_duration_ns);
    }

    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &sleep_deadline, NULL) == EINTR) {
    }

    if (spin_duration_ns <= 0) {
        return;
    }

    for (;;) {
        if (clock_gettime(CLOCK_MONOTONIC, &now) == -1) {
            perror("clock_gettime failed");
            exit(1);
        }
        if (compare_timespec(&now, deadline) >= 0) {
            break;
        }
    }
}

static void free_replay_packets(replay_packet_t *packets, int num_packets) {
    int i = 0;

    if (!packets) {
        return;
    }

    for (i = 0; i < num_packets; i++) {
        free(packets[i].packet_data);
    }

    free(packets);
}

static replay_packet_t *parse_pcap(const char *pcap_file, int *num_packets) {
    char errbuf[PCAP_ERRBUF_SIZE];
    pcap_t *handle = NULL;
    replay_packet_t *packets = NULL;
    int datalink = 0;
    int64_t last_ts_ns = -1;
    struct pcap_pkthdr *header = NULL;
    const u_char *data = NULL;
    size_t short_packet_count = 0;
    size_t truncated_packet_count = 0;
    size_t replay_overhead = 0;

    handle = pcap_open_offline(pcap_file, errbuf);
    if (!handle) {
        fprintf(stderr, "Error opening PCAP file: %s\n", errbuf);
        exit(1);
    }

    *num_packets = 0;
    datalink = pcap_datalink(handle);

    if (datalink == DLT_EN10MB) {
        replay_overhead = ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr);
    } else if (datalink == DLT_RAW) {
        replay_overhead = sizeof(struct iphdr) + sizeof(struct udphdr);
    } else {
        fprintf(
            stderr,
            "Warning: unsupported link-layer type %d for exact size matching. "
            "LateFrame will assume IPv4 + UDP overhead only.\n",
            datalink
        );
        replay_overhead = sizeof(struct iphdr) + sizeof(struct udphdr);
    }

    fprintf(
        stderr,
        "PCAP replay mode: each captured packet is encapsulated into UDP. "
        "LateFrame preserves as many bytes as possible from the end of the captured packet, "
        "so the replayed packet matches the original captured packet size whenever that length is at least %zu bytes.\n",
        replay_overhead
    );

    while (pcap_next_ex(handle, &header, &data) > 0) {
        replay_packet_t packet;
        replay_packet_t *new_packets = NULL;
        int64_t ts_ns = 0;
        size_t payload_size = 0;
        const u_char *preserved_bytes = NULL;

        if (header->len > header->caplen) {
            truncated_packet_count++;
            fprintf(
                stderr,
                "Warning: packet %d in %s was truncated in the capture (%u bytes captured, %u bytes on wire). "
                "Replay will preserve timing and size based on the captured bytes only.\n",
                *num_packets + 1,
                pcap_file,
                header->caplen,
                header->len
            );
        }

        if (header->caplen >= replay_overhead) {
            payload_size = (size_t)header->caplen - replay_overhead;
            preserved_bytes = data + replay_overhead;
        } else {
            short_packet_count++;
            payload_size = 0;
            preserved_bytes = NULL;
            fprintf(
                stderr,
                "Warning: packet %d in %s is only %u bytes long. "
                "Replay encapsulation needs %zu bytes, so the replayed UDP packet will be larger than the original capture.\n",
                *num_packets + 1,
                pcap_file,
                header->caplen,
                replay_overhead
            );
        }

        ts_ns = ((int64_t)header->ts.tv_sec * 1000000000LL) + ((int64_t)header->ts.tv_usec * 1000LL);

        memset(&packet, 0, sizeof(packet));
        packet.inter_arrival_ns = (last_ts_ns >= 0) ? (ts_ns - last_ts_ns) : 0;
        packet.packet_size = payload_size;
        packet.packet_data = malloc(payload_size);
        if (!packet.packet_data && payload_size > 0) {
            perror("malloc for packet replay data failed");
            free_replay_packets(packets, *num_packets);
            pcap_close(handle);
            exit(1);
        }

        if (payload_size > 0) {
            memcpy(packet.packet_data, preserved_bytes, payload_size);
        }

        new_packets = realloc(packets, (size_t)(*num_packets + 1) * sizeof(*packets));
        if (!new_packets) {
            perror("realloc for packet replay data failed");
            free(packet.packet_data);
            free_replay_packets(packets, *num_packets);
            pcap_close(handle);
            exit(1);
        }

        packets = new_packets;
        packets[*num_packets] = packet;
        (*num_packets)++;
        last_ts_ns = ts_ns;
    }

    fprintf(
        stderr,
        "PCAP replay summary: %d packets loaded, %zu packets shorter than the UDP header, %zu truncated packets.\n",
        *num_packets,
        short_packet_count,
        truncated_packet_count
    );

    pcap_close(handle);
    return packets;
}

static char **allocate_payloads(int num_packets, int packet_size) {
    char **payloads = NULL;
    int i = 0;

    payloads = calloc((size_t)num_packets, sizeof(*payloads));
    if (!payloads) {
        perror("Failed to allocate memory for payload array");
        return NULL;
    }

    for (i = 0; i < num_packets; i++) {
        char sequence[32];
        int sequence_len = 0;
        size_t copy_len = 0;

        payloads[i] = malloc((size_t)packet_size);
        if (!payloads[i]) {
            perror("Failed to allocate memory for payload");
            return payloads;
        }

        memset(payloads[i], 'A', (size_t)packet_size);

        sequence_len = snprintf(sequence, sizeof(sequence), "%d", i + 1);
        if (sequence_len < 0 || sequence_len >= (int)sizeof(sequence)) {
            fprintf(stderr, "Error: sequence number formatting failed.\n");
            return payloads;
        }

        copy_len = (size_t)sequence_len < (size_t)packet_size ? (size_t)sequence_len : (size_t)packet_size;
        memcpy(payloads[i], sequence, copy_len);
    }

    return payloads;
}

static void free_payloads(char **payloads, int num_packets) {
    int i = 0;

    if (!payloads) {
        return;
    }

    for (i = 0; i < num_packets; i++) {
        free(payloads[i]);
    }

    free(payloads);
}

static int send_packet(
    int sock,
    const void *payload,
    size_t payload_size,
    const struct sockaddr_in *dest_addr
) {
    ssize_t sent = sendto(
        sock,
        payload,
        payload_size,
        0,
        (const struct sockaddr *)dest_addr,
        sizeof(*dest_addr)
    );

    if (sent < 0) {
        perror("sendto failed");
        return -1;
    }

    if ((size_t)sent != payload_size) {
        fprintf(stderr, "Error: short UDP send (%zd of %zu bytes).\n", sent, payload_size);
        return -1;
    }

    return 0;
}

int main(int argc, char *argv[]) {
    int num_packets = 0;
    int packet_size = 0;
    int dest_port = 0;
    int spin_duration_us = 0;
    int num_packets_set = 0;
    int packet_size_set = 0;
    int dest_port_set = 0;
    int parameter_set = 0;
    int sigma_set = 0;
    int spin_duration_set = 0;
    int log_enabled = 0;
    int capture_enabled = 0;
    int absolute_timer_fd = -1;
    int sock = -1;
    int exit_code = 1;
    int i = 0;
    unsigned int seed = 0;
    traffic_mode_t traffic_mode = MODE_UNSET;
    wait_mode_t wait_mode = WAIT_MODE_TIMERFD;
    double parameter = 0.0;
    double sigma = 0.0;
    char *source_interface = NULL;
    char *dest_ip = NULL;
    char *pcap_file = NULL;
    char **payloads = NULL;
    replay_packet_t *packets = NULL;
    FILE *log_file = NULL;
    struct sockaddr_in dest_addr;
    struct timespec start_time;
    struct timespec next_deadline;
    int64_t spin_duration_ns = 0;
    const char *short_opts = "Vhn:i:d:p:a:S:s:lt:cf:";
    const struct option long_opts[] = {
        {"version", no_argument, NULL, 'V'},
        {"help", no_argument, NULL, 'h'},
        {"num-packets", required_argument, NULL, 'n'},
        {"num_packets", required_argument, NULL, 'n'},
        {"interface", required_argument, NULL, 'i'},
        {"destination", required_argument, NULL, 'd'},
        {"port", required_argument, NULL, 'p'},
        {"param", required_argument, NULL, 'a'},
        {"sigma", required_argument, NULL, 'S'},
        {"size", required_argument, NULL, 's'},
        {"log", no_argument, NULL, 'l'},
        {"distribution", required_argument, NULL, 't'},
        {"capture", no_argument, NULL, 'c'},
        {"pcap-file", required_argument, NULL, 'f'},
        {"pcap_file", required_argument, NULL, 'f'},
        {"wait-mode", required_argument, NULL, 1000},
        {"spin-us", required_argument, NULL, 1001},
        {"sping-us", required_argument, NULL, 1001},
        {"no-cpu-pin", no_argument, NULL, 1002},
        {NULL, 0, NULL, 0}
    };
    int opt = 0;

    opterr = 0;

    while ((opt = getopt_long(argc, argv, short_opts, long_opts, NULL)) != -1) {
        switch (opt) {
            case 'V':
                print_version();
                return 0;

            case 'h':
                print_help();
                return 0;

            case 'n':
                if (parse_positive_int(optarg, "-n/--num-packets", &num_packets) != 0) {
                    return 1;
                }
                num_packets_set = 1;
                break;

            case 'i':
                source_interface = optarg;
                break;

            case 'd':
                dest_ip = optarg;
                break;

            case 'p':
                if (parse_positive_int(optarg, "-p/--port", &dest_port) != 0) {
                    return 1;
                }
                dest_port_set = 1;
                break;

            case 'a':
                if (parse_non_negative_double(optarg, "-a/--param", &parameter) != 0) {
                    return 1;
                }
                parameter_set = 1;
                break;

            case 'S':
                if (parse_non_negative_double(optarg, "-S/--sigma", &sigma) != 0) {
                    return 1;
                }
                sigma_set = 1;
                break;

            case 's':
                if (parse_positive_int(optarg, "-s/--size", &packet_size) != 0) {
                    return 1;
                }
                packet_size_set = 1;
                break;

            case 'l':
                log_enabled = 1;
                break;

            case 't':
                traffic_mode = parse_mode(optarg);
                if (traffic_mode == MODE_UNSET) {
                    fprintf(stderr, "Error: unknown distribution '%s'. Use constant, poisson, gaussian, or pcap.\n", optarg);
                    return 1;
                }
                break;

            case 'c':
                capture_enabled = 1;
                break;

            case 'f':
                pcap_file = optarg;
                break;

            case 1000:
                wait_mode = parse_wait_mode(optarg);
                if (wait_mode == (wait_mode_t)-1) {
                    fprintf(stderr, "Error: unknown wait mode '%s'. Use timerfd or nanosleep.\n", optarg);
                    return 1;
                }
                break;

            case 1001:
                if (parse_non_negative_int(optarg, "--spin-us", &spin_duration_us) != 0) {
                    return 1;
                }
                spin_duration_set = 1;
                break;

            case 1002:
                cpu_pinning_enabled = 0;
                break;

            case '?':
                if (optopt != 0) {
                    fprintf(stderr, "Error: option '-%c' requires a valid argument.\n", optopt);
                } else {
                    fprintf(stderr, "Error: invalid option.\n");
                }
                print_help();
                return 1;

            default:
                print_help();
                return 1;
        }
    }

    if (traffic_mode == MODE_UNSET) {
        fprintf(stderr, "Error: -t/--distribution must be specified.\n");
        print_help();
        return 1;
    }

    if (!source_interface) {
        fprintf(stderr, "Error: -i/--interface is required.\n");
        print_help();
        return 1;
    }

    if (!dest_ip) {
        fprintf(stderr, "Error: -d/--destination is required.\n");
        print_help();
        return 1;
    }

    if (!dest_port_set) {
        fprintf(stderr, "Error: -p/--port is required.\n");
        print_help();
        return 1;
    }

    if (traffic_mode == MODE_PCAP) {
        if (!pcap_file) {
            fprintf(stderr, "Error: PCAP mode requires -f/--pcap-file.\n");
            print_help();
            return 1;
        }

        if (num_packets_set) {
            fprintf(stderr, "Warning: -n/--num-packets is ignored in pcap mode.\n");
        }
        if (packet_size_set) {
            fprintf(stderr, "Warning: -s/--size is ignored in pcap mode.\n");
        }
        if (parameter_set) {
            fprintf(stderr, "Warning: -a/--param is ignored in pcap mode.\n");
        }
        if (sigma_set) {
            fprintf(stderr, "Warning: -S/--sigma is ignored in pcap mode.\n");
        }
    } else {
        if (!num_packets_set) {
            fprintf(stderr, "Error: -n/--num-packets is required.\n");
            print_help();
            return 1;
        }
        if (!packet_size_set) {
            fprintf(stderr, "Error: -s/--size is required.\n");
            print_help();
            return 1;
        }
        if (!parameter_set) {
            fprintf(stderr, "Error: -a/--param is required.\n");
            print_help();
            return 1;
        }

        if (traffic_mode == MODE_GAUSSIAN && !sigma_set) {
            fprintf(stderr, "Error: Gaussian mode requires -S/--sigma.\n");
            print_help();
            return 1;
        }

        if (traffic_mode == MODE_CONSTANT && parameter <= 0.0) {
            fprintf(stderr, "Error: constant mode requires a strictly positive interval in ms.\n");
            return 1;
        }
        if (traffic_mode == MODE_POISSON && parameter <= 0.0) {
            fprintf(stderr, "Error: poisson mode requires a strictly positive lambda in packets/s.\n");
            return 1;
        }
        if (traffic_mode == MODE_GAUSSIAN && parameter <= 0.0) {
            fprintf(stderr, "Error: gaussian mode requires a strictly positive mean in ms.\n");
            return 1;
        }

        if (pcap_file) {
            fprintf(stderr, "Warning: -f/--pcap-file is ignored outside pcap mode.\n");
        }
    }

    if (wait_mode == WAIT_MODE_NANOSLEEP) {
        if (!spin_duration_set) {
            fprintf(stderr, "Error: --wait-mode nanosleep requires --spin-us.\n");
            return 1;
        }
    } else if (spin_duration_set) {
        fprintf(stderr, "Warning: --spin-us is ignored unless --wait-mode nanosleep is selected.\n");
    }

    if (log_enabled) {
        log_file = fopen(LOG_PATH, "w");
        if (!log_file) {
            perror("Failed to open log file");
            return 1;
        }
    }

    if (traffic_mode == MODE_PCAP) {
        packets = parse_pcap(pcap_file, &num_packets);
        if (num_packets == 0) {
            fprintf(stderr, "Error: no packets found in %s.\n", pcap_file);
            goto cleanup;
        }
    } else {
        payloads = allocate_payloads(num_packets, packet_size);
        if (!payloads) {
            goto cleanup;
        }

        for (i = 0; i < num_packets; i++) {
            if (!payloads[i]) {
                goto cleanup;
            }
        }
    }

    seed = (unsigned int)(time(NULL) ^ (unsigned int)getpid());
    srand(seed);

    set_realtime_priority(99);
    if (cpu_pinning_enabled) {
        uint8_t cpu_id = 0;
        set_cpu_affinity(cpu_id);
        printf("CPU pinning enabled: process is pinned to CPU %d\n", cpu_id);
    }

    sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("Socket creation failed");
        goto cleanup;
    }

    set_non_blocking(sock);

    if (setsockopt(sock, SOL_SOCKET, SO_BINDTODEVICE, source_interface, strlen(source_interface) + 1) < 0) {
        perror("Bind to device failed");
        goto cleanup;
    }

    if (capture_enabled) {
        start_packet_capture(source_interface, dest_ip);
    }

    memset(&dest_addr, 0, sizeof(dest_addr));
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons((uint16_t)dest_port);
    if (inet_pton(AF_INET, dest_ip, &dest_addr.sin_addr) != 1) {
        fprintf(stderr, "Error: invalid destination IPv4 address '%s'.\n", dest_ip);
        goto cleanup;
    }

    if (wait_mode == WAIT_MODE_TIMERFD) {
        absolute_timer_fd = create_timerfd();
        if (absolute_timer_fd < 0) {
            goto cleanup;
        }
    }

    if (clock_gettime(CLOCK_MONOTONIC, &start_time) == -1) {
        perror("clock_gettime failed");
        goto cleanup;
    }

    next_deadline = start_time;
    spin_duration_ns = (int64_t)spin_duration_us * 1000LL;

    for (i = 0; i < num_packets; i++) {
        int64_t interval_ns = 0;
        const void *packet_data = NULL;
        size_t current_packet_size = 0;

        if (traffic_mode == MODE_CONSTANT) {
            interval_ns = (int64_t)llround(parameter * 1e6);
            packet_data = payloads[i];
            current_packet_size = (size_t)packet_size;
        } else if (traffic_mode == MODE_POISSON) {
            interval_ns = (int64_t)llround(generate_exponential(parameter) * 1e9);
            packet_data = payloads[i];
            current_packet_size = (size_t)packet_size;
        } else if (traffic_mode == MODE_GAUSSIAN) {
            interval_ns = generate_gaussian_ns(parameter, sigma);
            packet_data = payloads[i];
            current_packet_size = (size_t)packet_size;
        } else {
            interval_ns = packets[i].inter_arrival_ns;
            packet_data = packets[i].packet_data;
            current_packet_size = packets[i].packet_size;
        }

        add_ns(&next_deadline, interval_ns);
        if (wait_mode == WAIT_MODE_TIMERFD) {
            set_timer_absolute(absolute_timer_fd, &next_deadline);
            wait_for_next_tick(absolute_timer_fd);
        } else {
            wait_until_deadline(&next_deadline, spin_duration_ns);
        }

        if (send_packet(sock, packet_data, current_packet_size, &dest_addr) != 0) {
            goto cleanup;
        }

        if (log_enabled) {
            fprintf(log_file, "packet=%d size=%zu destination=%s:%d\n", i + 1, current_packet_size, dest_ip, dest_port);
            fflush(log_file);
            printf("packet=%d size=%zu destination=%s:%d\n", i + 1, current_packet_size, dest_ip, dest_port);
        }
    }

    exit_code = 0;

cleanup:
    if (absolute_timer_fd >= 0) {
        close(absolute_timer_fd);
    }
    if (sock >= 0) {
        close(sock);
    }
    if (capture_enabled) {
        stop_packet_capture();
    }
    if (log_file) {
        fclose(log_file);
    }

    free_payloads(payloads, num_packets);
    free_replay_packets(packets, num_packets);

    return exit_code;
}
