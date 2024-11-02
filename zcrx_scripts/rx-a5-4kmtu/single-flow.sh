#!/bin/bash
# Get the dir of this project
DIR=$(realpath $(dirname $(readlink -f $0))/../..)



# Parse arguments
# Example: ./single-flow.sh enp37s0f1
iface=${1:-enp94s0f1np1}
results_dir=${2:-$DIR/results}

# Create results directory
mkdir -p $results_dir


# TSO + aRFS + 4K MTU
# echo "network_setup.py $iface --gro --tso --mtu 4096"
# ${DIR}/network_setup.py $iface --gro --tso --mtu 4096
echo "run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/mlx4k_rx | tee ${results_dir}/mlx4k_rx.log"
${DIR}/run_experiment_receiver.py --throughput --utilisation --util-breakdown --output ${results_dir}/mlx4k_rx | tee ${results_dir}/mlx4k_rx.log

