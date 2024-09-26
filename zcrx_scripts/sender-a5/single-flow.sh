#!/bin/bash
# Get the dir of this project
DIR=$(realpath $(dirname $(readlink -f $0))/../..)

# Parse arguments
# Example: ./single-flow.sh 128.84.155.115 192.168.10.115 enp37s0f1
public_dst_ip=${1:-128.46.36.210}
device_dst_ip=${2:-10.0.1.3}
iface=${3:-enp94s0f1np1}
results_dir=${4:-$DIR/results}

# Create results directory
mkdir -p $results_dir


# No TSO
echo "network_setup.py $iface --no-tso"
$DIR/network_setup.py $iface --no-tso
echo "run_experiment_receiver.py --throughput --utilisation --util-breakdown --output $results_dir/single-flow_tsogro | tee $results_dir/single-flow_notso.log"
$DIR/run_experiment_receiver.py --throughput --utilisation --output $results_dir/single-flow_jumbo | tee $results_dir/single-flow_notso.log


# # TSO/GRO
# echo "$DIR/network_setup.py $iface --gro --tso"
# $DIR/network_setup.py $iface --gro --tso
# echo "$DIR/run_experiment_sender.py --receiver $public_dst_ip --addr $device_dst_ip --throughput --utilisation --util-breakdown --output $results_dir/single-flow_tsogro | tee $results_dir/single-flow_tsogro.log"
# $DIR/run_experiment_sender.py --receiver $public_dst_ip --addr $device_dst_ip --throughput --utilisation --util-breakdown --output $results_dir/single-flow_tsogro | tee $results_dir/single-flow_tsogro.log

# # TSO/GRO+Jumbo Frame
# $DIR/network_setup.py $iface --gro --tso
# $DIR/run_experiment_sender.py --receiver $public_dst_ip --addr $device_dst_ip --throughput --utilisation --util-breakdown --output $results_dir/single-flow_tsogro+jumbo | tee $results_dir/single-flow_tsogro+jumbo.log

# # TSO/GRO+Jumbo Frame+aRFS
# $DIR/network_setup.py $iface --gro --tso
# $DIR/run_experiment_sender.py --receiver $public_dst_ip --addr $device_dst_ip --throughput --utilisation --util-breakdown --arfs --output $results_dir/single-flow_all-opts | tee $results_dir/single-flow_all-opts.log

# Print results
# $DIR/scripts/parse/single-flow.sh $results_dir
