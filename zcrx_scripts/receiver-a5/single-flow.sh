#!/bin/bash
# Get the dir of this project
DIR=$(realpath $(dirname $(readlink -f $0))/../..)

# Parse arguments
# Example: ./single-flow.sh enp37s0f1
iface=${1:-enp94s0f1np1}
results_dir=${2:-$DIR/results}

# Create results directory
mkdir -p $results_dir


# No TSO + aRFS
echo "network_setup.py $iface --no-tso"
${DIR}/network_setup.py $iface --no-tso
echo "run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/single-flow_notso | tee ${results_dir}/single-flow_notso.log"
${DIR}/run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/single-flow_notso | tee ${results_dir}/single-flow_notso.log


# TSO + aRFS
echo "network_setup.py $iface --tso"
${DIR}/network_setup.py $iface --tso
echo "run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/single-flow_tsogro | tee ${results_dir}/single-flow_tsogro.log"
${DIR}/run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/single-flow_tsogro | tee ${results_dir}/single-flow_tsogro.log

# # TSO/GRO+Jumbo Frame
# $DIR/network_setup.py $iface --gro --tso
# $DIR/run_experiment_receiver.py --throughput --utilisation --util-breakdown --output $results_dir/single-flow_tsogro+jumbo | tee $results_dir/single-flow_tsogro+jumbo.log

# aRFS should be enabled by default
# # TSO/GRO+aRFS
# $DIR/network_setup.py $iface --arfs --mtu 1500
# $DIR/run_experiment_receiver.py --throughput --utilisation --arfs --output $results_dir/single-flow_tsogro+arfs | tee $results_dir/single-flow_tsogro+arfs.log

# # TSO/GRO+Jumbo Frame+aRFS
# $DIR/network_setup.py $iface --gro --tso
# $DIR/run_experiment_receiver.py --throughput --utilisation --util-breakdown --arfs --output $results_dir/single-flow_all-opts | tee $results_dir/single-flow_all-opts.log
