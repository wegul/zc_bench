#!/usr/bin/env python3
import argparse
import os
import shlex
import signal
import subprocess as _sp
import tempfile
import threading
import time
import xmlrpc.client
from process_output import *


# For debugging
class subprocess:
    PIPE = _sp.PIPE
    DEVNULL = _sp.DEVNULL
    STDOUT = _sp.STDOUT
    @staticmethod
    def Popen(*args, **kwargs):
        print("+ " + " ".join(shlex.quote(s) for s in args[0]))
        return _sp.Popen(*args, **kwargs)


# Constants
SENDER_COMM_PORT = 8080
IPERF_BASE_PORT = 30000
NETPERF_BASE_PORT = 40000
PERF_PATH = "/home/shubham/bin/perf"
FLAME_PATH = "/home/shubham/utils/FlameGraph"
PERF_DATA = "perf.data"
CPUS = [0, 4, 8, 12, 2, 6, 10, 14]
MAX_CONNECTIONS = len(CPUS)
MAX_RPCS = 16


def parse_args():
    parser = argparse.ArgumentParser(description="Run TCP measurement experiments on the sender.")

    # Add arguments
    parser.add_argument("--receiver", required=True, type=str, help="Address of the receiver to communicate metadata.")
    parser.add_argument("--addr", required=True, type=str, help="Address of the receiver to run experiments on.")
    parser.add_argument("--config", choices=["one-to-one", "incast", "outcast", "all-to-all"], default="one-to-one", help="Configuration to run the experiment with.")
    parser.add_argument("--num-connections", type=int, default=1, help="Number of connections.")
    parser.add_argument("--num-rpcs", type=int, default=0, help="Number of RPC style connections.")
    parser.add_argument('--arfs', action='store_true', default=False, help='This experiment is run with aRFS.')
    parser.add_argument("--duration", type=int, default=20, help="Duration of the experiment in seconds.")
    parser.add_argument("--window", type=int, default=None, help="Specify the TCP window size in KiB.")
    parser.add_argument("--output", type=str, default=None, help="Write raw output to the directory.")
    parser.add_argument("--throughput", action="store_true", help="Measure throughput in Gbps.")
    parser.add_argument("--utilisation", action="store_true", help="Measure CPU utilisation in percent.")
    parser.add_argument("--cache-miss", action="store_true", help="Measure LLC miss rate in percent.")
    parser.add_argument("--util-breakdown", action="store_true", help="Calculate CPU utilisation breakdown.")
    parser.add_argument("--cache-breakdown", action="store_true", help="Calculate cache miss breakdown.")
    parser.add_argument("--flame", action="store_true", help="Create a flame graph from the experiment.")
    parser.add_argument("--latency", action="store_true", help="Calculate the average data copy latency for each packet.")

    # Parse and verify arguments
    args = parser.parse_args()

    if not (1 <= args.num_connections <= MAX_CONNECTIONS):
        print("Can't set --num-connections outside of [1, {}].".format(MAX_CONNECTIONS))
        exit(1)

    if not (0 <= args.num_rpcs <= MAX_RPCS):
        print("Can't set --num-rpcs outside of [0, {}].".format(MAX_RPCS))
        exit(1)

    if args.num_rpcs > 0 and args.num_connections > 1:
        print("Can't use more than 1 --num-connections if using --num-rpcs.")
        exit(1)

    if not (5 <= args.duration <= 60):
        print("Can't set --duration outside of [5, 60].")
        exit(1)

    if args.flame and args.output is None:
        print("Please provide --output if using --flame.")
        exit(1)

    # Create the directory for writing raw outputs
    if args.output is not None:
        os.makedirs(args.output, exist_ok=True)

    # Set CPUs to be used
    if args.config != "outcast":
        args.cpus = CPUS[:args.num_connections]
    else:
        args.cpus = CPUS[:1]

    # Set IRQ processing CPUs
    if args.arfs:
        args.affinity = []
    else:
        args.affinity = [cpu + 1 for cpu in args.cpus]

    # Create a list of experiments
    args.experiments = []
    if args.throughput:
        args.experiments.append("throughput")
    if args.utilisation:
        args.experiments.append("utilisation")
    if args.cache_miss:
        args.experiments.append("cache miss")
    if args.util_breakdown:
        args.experiments.append("util breakdown")
    if args.cache_breakdown:
        args.experiments.append("cache breakdown")
    if args.flame:
        args.experiments.append("flame")
    if args.latency:
        args.experiments.append("latency")

    # Return parsed and verified arguments
    return args


# Convenience functions
def clear_processes():
    os.system("pkill iperf")
    os.system("pkill perf")
    os.system("pkill sar")


def run_iperf(cpu, addr, port, duration, window):
    if window is None:
        args = ["taskset", "-c", str(cpu), "iperf", "-i", "1", "-c", addr, "-t", str(duration), "-p", str(port)]
    else:
        args = ["taskset", "-c", str(cpu), "iperf", "-i", "1", "-c", addr, "-t", str(duration), "-p", str(port), "-w", str(window / 2) + "K"]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_iperfs(config, addr, num_connections, cpus, duration, window):
    if config in ["one-to-one", "incast"]:
        iperfs = [run_iperf(cpu, addr, IPERF_BASE_PORT + n, duration, window) for n, cpu in enumerate(cpus)]
    elif config == "outcast":
        iperfs = [run_iperf(cpus[0], addr, IPERF_BASE_PORT + n, duration, window) for n in range(num_connections)]
    elif config == "all-to-all":
        iperfs = []
        for i, sender_cpu in enumerate(cpus):
            for j, receiver_cpu in enumerate(cpus):
                iperfs.append(run_iperf(sender_cpu, addr, IPERF_BASE_PORT + MAX_CONNECTIONS * i + j, duration, window))
    return iperfs


def run_netperf(cpu, addr, port, duration):
    args = ["taskset", "-c", str(cpu), "netperf", "-H", addr, "-t", "TCP_RR", "-l", str(duration), "-p", str(port), "--", "-r", "4000,4000", "-o", "throughput,P50_LATENCY,P90_LATENCY,P99_LATENCY"]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_netperfs(cpu, addr, num_rpcs, duration):
    return [run_netperf(cpu, addr, NETPERF_BASE_PORT + i, duration) for i in range(num_rpcs)]


def run_perf_cache(cpus):
    args = [PERF_PATH, "stat", "-C", ",".join(map(str, set(cpus))), "-e", "LLC-loads,LLC-load-misses,LLC-stores,LLC-store-misses"]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_perf_record_util(cpus, perf_data_file):
    args = [PERF_PATH, "record", "-C", ",".join(map(str, set(cpus))), "-o", str(perf_data_file)]
    return subprocess.Popen(args, stdout=None, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_perf_record_cache(cpus, perf_data_file):
    args = [PERF_PATH, "record", "-e", "cache-misses", "-C", ",".join(map(str, set(cpus))), "-o", str(perf_data_file)]
    return subprocess.Popen(args, stdout=None, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_perf_record_flame(cpus, perf_data_file):
    args = [PERF_PATH, "record", "-g", "-F", "99", "-C", ",".join(map(str, set(cpus))), "-o", str(perf_data_file)]
    return subprocess.Popen(args, stdout=None, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_perf_report(perf_data_file):
    args = ["bash", "-c", "{} report --stdio --stdio-color never --percent-limit 0.01 -i {} | cat".format(PERF_PATH, perf_data_file)]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


def run_flamegraph(perf_data_file, output_svg_file):
    os.system("{} script -i {} | {}/stackcollapse-perf.pl > out.perf-folded".format(PERF_PATH, perf_data_file, FLAME_PATH))
    os.system("{}/flamegraph.pl out.perf-folded > {}".format(FLAME_PATH, output_svg_file))


def run_sar(cpus):
    args = ["sar", "-u", "-P", ",".join(map(str, set(cpus))), "1", "1000"]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, universal_newlines=True)


if __name__ == "__main__":
    # Parse args
    args = parse_args()

    # Create the XMLRPC proxy
    receiver = xmlrpc.client.ServerProxy("http://{}:{}".format(args.receiver, SENDER_COMM_PORT), allow_none=True)

    # Wait till receiver is ready
    while True:
        try:
            receiver.system.listMethods()
            break
        except ConnectionRefusedError:
            time.sleep(1)

    # Run the experiments
    clear_processes()
    header = []
    output = []
    if args.throughput:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[throughput] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Wait till all iperfs and netperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()
        print("[throughput] finished experiment.")

        # Process and write the raw output
        total_throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "throughput_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            total_throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "throughput_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        # Print the output
        print("[throughput] total throughput: {:.3f}".format(total_throughput))
        header.append("throughput (Gbps)")
        output.append("{:.3f}".format(total_throughput))

    if args.utilisation:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[utilisation] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Start the sar instance
        sar = run_sar(args.cpus + args.affinity)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()

        # Kill the sar instance
        sar.send_signal(signal.SIGINT)
        sar.wait()
        print("[utilisation] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "utilisation_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "utilisation_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        lines = sar.stdout.readlines()
        cpu_util = sum(process_sar_output(lines).values())
        if args.output is not None:
            with open(os.path.join(args.output, "utilisation_sar.log"), "w") as f:
                f.writelines(lines)

        # Print the output
        print("[utilisation] total throughput: {:.3f}\tutilisation: {:.3f}".format(throughput, cpu_util))
        header.append("sender utilisation (%)")
        output.append("{:.3f}".format(cpu_util))

    if args.cache_miss:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[cache miss] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Start the perf instance
        perf = run_perf_cache(args.cpus + args.affinity)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()

        # Kill the perf instance
        perf.send_signal(signal.SIGINT)
        perf.wait()
        print("[cache miss] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "cache-miss_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "cache-miss_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        lines = perf.stdout.readlines()
        cache_miss = process_perf_cache_output(lines)
        if args.output is not None:
            with open(os.path.join(args.output, "cache-miss_perf.log"), "w") as f:
                f.writelines(lines)

        # Print the output
        print("[cache miss] total throughput: {:.3f}\tcache miss: {:.3f}".format(throughput, cache_miss))
        header.append("sender cache miss (%)")
        output.append("{:.3f}".format(cache_miss))

    if args.util_breakdown:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[util breakdown] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Start the perf instance
        output_dir = tempfile.TemporaryDirectory()
        perf_data_file = os.path.join(output_dir.name, PERF_DATA)
        perf = run_perf_record_util(args.cpus + args.affinity, perf_data_file)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()

        # Kill the perf instance
        perf.send_signal(signal.SIGINT)
        perf.wait()
        print("[util breakdown] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "util-breakdown_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "util-breakdown_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        # Run a perf report instance
        perf = run_perf_report(perf_data_file)
        lines = []
        while True:
            new_lines =  perf.stdout.readlines()
            lines += new_lines
            if len(new_lines) == 0:
                break
        perf.wait()
        output_dir.cleanup()
        total_contrib, unaccounted_contrib, util_contibutions, not_found = process_perf_report_output(lines)
        if args.output is not None:
            with open(os.path.join(args.output, "util-breakdown_perf.log"), "w") as f:
                f.writelines(lines)

        # Print the output
        print("[util breakdown] total throughput: {:.3f}\ttotal contribution: {:.3f}\tunaccounted contribution: {:.3f}".format(throughput, total_contrib, unaccounted_contrib))
        if unaccounted_contrib > 5:
            print("[util breakdown] unknown symbols: {}".format(", ".join(not_found)))

    if args.cache_breakdown:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[cache breakdown] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Start the perf instance
        output_dir = tempfile.TemporaryDirectory()
        perf_data_file = os.path.join(output_dir.name, PERF_DATA)
        perf = run_perf_record_cache(args.cpus + args.affinity, perf_data_file)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()

        # Kill the perf instance
        perf.send_signal(signal.SIGINT)
        perf.wait()
        print("[cache breakdown] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "cache-breakdown_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "cache-breakdown_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        # Run a perf report instance
        perf = run_perf_report(perf_data_file)
        lines = []
        while True:
            new_lines =  perf.stdout.readlines()
            lines += new_lines
            if len(new_lines) == 0:
                break
        perf.wait()
        total_contrib, unaccounted_contrib, cache_contibutions, not_found = process_perf_report_output(lines)
        if args.output is not None:
            with open(os.path.join(args.output, "cache-breakdown_perf.log"), "w") as f:
                f.writelines(lines)

        # Print the output
        print("[cache breakdown] total throughput: {:.3f}\ttotal contribution: {:.3f}\tunaccounted contribution: {:.3f}".format(throughput, total_contrib, unaccounted_contrib))
        if unaccounted_contrib > 5:
            print("[cache breakdown] unknown symbols: {}".format(", ".join(not_found)))

    if args.flame:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[flame] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Start the perf instance
        output_dir = tempfile.TemporaryDirectory()
        perf_data_file = os.path.join(output_dir.name, PERF_DATA)
        perf = run_perf_record_flame(args.cpus + args.affinity, perf_data_file)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()

        # Kill the perf instance
        perf.send_signal(signal.SIGINT)
        perf.wait()
        print("[flame] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "flame_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "flame_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        # Run a perf report instance
        output_svg_file = os.path.join(args.output, "flame.svg")
        run_flamegraph(perf_data_file, output_svg_file)
        output_dir.cleanup()

        # Print the output
        print("[flame] total throughput: {:.3f}".format(throughput))

    if args.latency:
        # Wait till receiver starts
        receiver.mark_sender_ready()
        receiver.is_receiver_ready()
        print("[latency] starting experiment...")
        
        # Start iperf instances
        iperfs = run_iperfs(args.config, args.addr, args.num_connections, args.cpus, args.duration, args.window)

        # Start netperf instances
        netperfs = run_netperfs(args.cpus[0], args.addr, args.num_rpcs, args.duration)

        # Wait till all iperfs finish
        for p in iperfs + netperfs:
            p.wait()

        # Sender is done sending
        receiver.mark_sender_done()
        print("[latency] finished experiment.")

        # Process and write the raw output
        throughput = 0
        for i, p in enumerate(iperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "latency_iperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)
            throughput += process_iperf_output(lines)
        for i, p in enumerate(netperfs):
            lines = p.stdout.readlines()
            if args.output is not None:
                with open(os.path.join(args.output, "latency_netperf_{}.log".format(i)), "w") as f:
                    f.writelines(lines)

        # Print the output
        print("[latency] total throughput: {:.3f}".format(throughput))

    # Sync with receiver before exiting
    receiver.is_receiver_ready()
    receiver.mark_sender_ready()

    # Sleep before beginning the next experiment
    time.sleep(1)

    # Print final stats
    if len(header) > 0:
        print("\t".join(header))
        print("\t".join(output))

    # Print utilisation breakdown if required
    if args.util_breakdown:
        keys = sorted(util_contibutions.keys())
        print("\t".join(keys))
        print("\t".join(["{:.3f}".format(util_contibutions[k]) for k in keys]))

    # Print cache breakdown if required
    if args.cache_breakdown:
        keys = sorted(cache_contibutions.keys())
        print("\t".join(keys))
        print("\t".join(["{:.3f}".format(cache_contibutions[k]) for k in keys]))
