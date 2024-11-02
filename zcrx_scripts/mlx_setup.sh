# Set MTU=4096
echo "sudo ip link set dev enp94s0f1np1 mtu 4096"
sudo ip link set dev enp94s0f1np1 mtu 4096

# Set RSS == aRFS
echo "sudo ethtool -X enp94s0f1np1 equal 1"
sudo ethtool -X enp94s0f1np1 equal 1

echo "sudo set_irq_affinity.sh enp94s0f1np1"
sudo set_irq_affinity.sh enp94s0f1np1
