#!/usr/bin/env python3

import argparse
import os as _os


# For debugging
class os:
    @staticmethod
    def system(p, log=True):
        if log: print("+ " + p)
        _os.system(p)


# In the default IRQ affinity config mode
# ID of the RX queue for each CPU 0-23
CPU_TO_RX_QUEUE_MAP = [int(i) for i in "0 6 7 8 1 9 10 11 2 12 13 14 3 15 16 17 4 18 19 20 5 21 22 23".split()]


# In the default IRO affinity config mode
# (one of) the RX queue(s) which has DMA on NUMA 0-3
NUMA_TO_RX_QUEUE_MAP = [int(i) for i in "0 6 7 8".split()]


# DDIO IO WAYS LLC mm register location
DDIO_REG = 0xc8b


# Base port of using iperf and netperf
IPERF_BASE_PORT = 30000
NETPERF_BASE_PORT = 40000


# Maximum number of connections
CPUS = [0, 2, 4, 6, 8, 12, 14, 16, 18, 20, 22]
MAX_CONNECTIONS = len(CPUS)
MAX_RPCS = 16


def parse_args():
    parser = argparse.ArgumentParser(description="Configure the network interface.")

    # Configuration for setting the IRQ affinity
    parser.add_argument('--sender', action='store_true', default=None, help='This is the sender.')
    parser.add_argument('--receiver', action='store_true', default=None, help='This is the receiver.')
    parser.add_argument("--config", choices=["one-to-one", "incast", "outcast", "all-to-all"], default="one-to-one", help="Configuration to run the experiment with.")

    # Parse basic parameters
    parser.add_argument('interface', type=str, help='The network device interface to configure.')
    parser.add_argument('--mtu', type=int, default=None, help='MTU of the network interface (in bytes).')
    parser.add_argument('--speed', type=int, default=None, help='Speed of the network interface (in Mbps).')
    parser.add_argument('--sock-size', action='store_true', default=None, help='Increase socket read/write memory limits.')
    parser.add_argument('--dca', type=int, default=None, help='Set the number of cache ways DCA/DDIO can use.')
    parser.add_argument('--ring-buffer', type=int, default=None, help='Set the size of the RX/TX ring buffer.')

    # Parse offload parameters
    parser.add_argument('--gro', action='store_true', default=None, help='Enables GRO.')
    parser.add_argument('--no-gro', dest='gro', action='store_false', default=None, help='Disables GRO.')
    parser.add_argument('--gso', action='store_true', default=None, help='Enables GSO.')
    parser.add_argument('--no-gso', dest='gso', action='store_false', default=None, help='Disables GRO.')
    parser.add_argument('--lro', action='store_true', default=None, help='Enables LRO.')
    parser.add_argument('--no-lro', dest='lro', action='store_false', default=None, help='Disables LRO.')
    parser.add_argument('--tso', action='store_true', default=None, help='Enables TSO.')
    parser.add_argument('--no-tso', dest='tso', action='store_false', default=None, help='Disables TSO.')
    parser.add_argument('--checksum', action='store_true', default=None, help='Enables checksumming offloads.')
    parser.add_argument('--no-checksum', dest='checksum', action='store_false', default=None, help='Disables checksumming offloads.')

    # Parse IRQ/aRFS parameters
    parser.add_argument('--arfs', action='store_true', default=None, help='Enables aRFS.')
    parser.add_argument('--no-arfs', dest='arfs', action='store_false', default=None, help='Disables aRFS.')

    # Actually parse arguments
    args = parser.parse_args()

    # Report errors
    if args.dca is not None and not (1 <= args.dca <= 11):
        print("Can't set --dca values outside of [1, 11].")
        exit(1)

    if args.arfs is not None and not args.arfs:
        if args.sender is not None and args.receiver is not None:
            print("Can't set both --sender and --receiver.")
            exit(1)

        if args.config is None:
            print("Must set --config when using --no-arfs.")
            exit(1)

        if args.sender is None and args.receiver is None:
            print("Must set one of --sender or --receiver with --no-arfs.")
            exit(1)

    if args.mtu is not None and not (0 < args.mtu <= 9000):
        print("Can't set values of --mtu outside of (0, 9000] bytes.")
        exit(1)

    if args.speed is not None and not (0 < args.speed <= 100000):
        print("Can't set values of --speed outside of (0, 100000] Mbps.")
        exit(1)

    if args.ring_buffer is not None and not (0 < args.ring_buffer <= 8192):
        print("Can't set values of --ring-buffer outside of (0, 1892].")
        exit(1)

    if args.tso is not None and args.checksum is not None and not args.checksum and args.tso:
        print("Can't use --no-checksum with --tso, --no-checksum implies --no-tso.")
        exit(1)

    if args.checksum is not None and not args.checksum:
        args.tso = False

    # Return validated arguments
    return args


# Convenience functions
def on_or_off(state):
    return "on" if state else "off"


def stop_irq_balance():
    os.system("service irqbalance stop")


def manage_ntuple(iface, enabled):
    os.system("ethtool -K {} ntuple {}".format(iface, on_or_off(enabled)))


def manage_rps(iface, enabled):
    num_rps = 32768 if enabled else 0
    os.system("echo {} > /proc/sys/net/core/rps_sock_flow_entries".format(num_rps))
    os.system("for f in /sys/class/net/{}/queues/rx-*/rps_flow_cnt; do echo {} > $f; done".format(iface, num_rps))


def set_irq_affinity(iface):
    os.system("set_irq_affinity.sh {} 2> /dev/null > /dev/null".format(iface))


def ntuple_send_port_to_queue(iface, port, n, loc):
    os.system("ethtool -U {} flow-type tcp4 dst-port {} action {} loc {}".format(iface, port, n, loc))
    os.system("ethtool -U {} flow-type tcp4 src-port {} action {} loc {}".format(iface, port, n, loc + MAX_CONNECTIONS * MAX_CONNECTIONS + MAX_RPCS))


def ntuple_clear_rules(iface):
    for i in range(2 * (MAX_CONNECTIONS * MAX_CONNECTIONS + MAX_RPCS)):
        os.system("ethtool -U {} delete {} 2> /dev/null > /dev/null".format(iface, i), False)


# Functions to set IRQ mode
def setup_irq_mode_arfs(iface):
    stop_irq_balance()
    manage_rps(iface, True)
    manage_ntuple(iface, True)
    set_irq_affinity(iface)
    ntuple_clear_rules(iface)


def setup_irq_mode_no_arfs_sender(iface, config):
    stop_irq_balance()
    manage_rps(iface, False)
    manage_ntuple(iface, True)
    ntuple_clear_rules(iface)
    set_irq_affinity(iface)
    if config in ["one-to-one", "incast"]:
        cpus = [(cpu, IPERF_BASE_PORT + n) for n, cpu in enumerate(CPUS)]
    if config == "outcast":
        cpus = [(CPUS[0], IPERF_BASE_PORT + n) for n in range(MAX_CONNECTIONS)]
    if config == "all-to-all":
        cpus = []
        for i, sender_cpu in enumerate(CPUS):
            for j, receiver_cpu in enumerate(CPUS):
                cpus.append((sender_cpu, IPERF_BASE_PORT + MAX_CONNECTIONS * i + j))
    for n, (cpu, port) in enumerate(cpus):
        ntuple_send_port_to_queue(iface, port, CPU_TO_RX_QUEUE_MAP[cpu + 1], n)
    for n in range(MAX_RPCS):
        ntuple_send_port_to_queue(iface, NETPERF_BASE_PORT + n, CPU_TO_RX_QUEUE_MAP[CPUS[0] + 1], 2 * MAX_CONNECTIONS * MAX_CONNECTIONS + n)


def setup_irq_mode_no_arfs_receiver(iface, config):
    stop_irq_balance()
    manage_rps(iface, False)
    manage_ntuple(iface, True)
    ntuple_clear_rules(iface)
    set_irq_affinity(iface) 
    if config in ["one-to-one", "outcast"]:
        cpus = [(cpu, IPERF_BASE_PORT + n) for n, cpu in enumerate(CPUS)]
    if config == "incast":
        cpus = [(CPUS[0], IPERF_BASE_PORT + n) for n in range(MAX_CONNECTIONS)]
    if config == "all-to-all":
        cpus = []
        for i, sender_cpu in enumerate(CPUS):
            for j, receiver_cpu in enumerate(CPUS):
                cpus.append((receiver_cpu, IPERF_BASE_PORT + MAX_CONNECTIONS * i + j))
    for n, (cpu, port) in enumerate(cpus):
        ntuple_send_port_to_queue(iface, port, CPU_TO_RX_QUEUE_MAP[cpu + 1], n)
    for n in range(MAX_RPCS):
        ntuple_send_port_to_queue(iface, NETPERF_BASE_PORT + n, CPU_TO_RX_QUEUE_MAP[CPUS[0] + 1], 2 * MAX_CONNECTIONS * MAX_CONNECTIONS + n)


def setup_affinity_mode(iface, arfs, sender, receiver, config):
    if arfs is not None:
        if arfs:
            setup_irq_mode_arfs(iface)
        elif sender is not None and sender:
            setup_irq_mode_no_arfs_sender(iface, config)
        elif receiver is not None and receiver:
            setup_irq_mode_no_arfs_receiver(iface, config)


# Set connection speed
def set_speed(iface, speed):
    if speed is not None:
        os.system("ethtool -s {} speed {} autoneg off".format(iface, speed))


# Set MTU
def set_mtu(iface, mtu):
    if mtu is not None:
        os.system("ifconfig {} mtu {}".format(iface, mtu))


# Set RX/RX ring buffer size
def set_ring_buffer_size(iface, size):
    if size is not None:
        os.system("ethtool -G {0} rx {1} tx {1}".format(iface, size))


# Functions to manage offloads
def manage_offloads(iface, lro, tso, gso, gro, checksum):
    offloads = {"lro": lro, "tso": tso, "gso": gso, "gro": gro, "tx": checksum, "rx": checksum}
    args = ["{} {}".format(offload, on_or_off(enabled)) for offload, enabled in offloads.items() if enabled is not None]
    if len(args) > 0:
        os.system("ethtool -K {} {}".format(iface, " ".join(args)))


# Increase socket memory size limit
def increase_sock_size_limit(enabled):
    if enabled:
        os.system("sysctl -w net.core.wmem_max=12582912 && sysctl -w net.core.rmem_max=12582912")


# Set DDIO ways
def set_ddio_ways(ways):
    if ways is not None:
        os.system("modprobe msr")
        os.system("wrmsr {} {}".format(DDIO_REG, hex((2 ** ways - 1) << (11 - ways))))


# Run the functions according to parsed arguments
if __name__ == "__main__":
    args = parse_args()

    # Set offload config
    manage_offloads(args.interface, args.lro, args.tso, args.gso, args.gro, args.checksum)

    # Set IRQ config
    setup_affinity_mode(args.interface, args.arfs, args.sender, args.receiver, args.config)

    # Setup other config
    set_speed(args.interface, args.speed)
    set_mtu(args.interface, args.mtu)
    increase_sock_size_limit(args.sock_size)
    set_ddio_ways(args.dca)
    set_ring_buffer_size(args.interface, args.ring_buffer)
