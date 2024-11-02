# ZCRX benchmark tool:

This folder only generates profiling results for MLNX and Vanilla/KernSplit CRDM NIC because it supports only iperf.
If you want FTP, please go to `profiler` and `pkt_forge`

Now, if you want to run iperf for golden baseline. Please go to `zcrx_scripts/rx-a5-4kmtu` or `zcrx_scripts/tx-a3-4kmtu`.
Please note that you have to configure the MLNX NIC accordingly by `zcrx_scripts/mlx_setup.sh`.