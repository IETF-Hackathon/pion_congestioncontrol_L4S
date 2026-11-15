# Your own Pion sender server

Created and verified on 14 November 2026. Go tests, vet, race checks, nine
Python tests and a Linux Prague/CCFB media call passed. The receiver's last
sample showed 377 ECT(1) RTP packets; the sender's last sample showed 195
feedback reports. These are local VM observations. See the
[recorded Linux verification](../../results/preview/linux-server-20261114.json).

The project can create an isolated Ubuntu 24.04 ARM64 server on this Apple
Silicon Mac using the installed Lima runtime. It has two virtual CPUs, 3 GiB
RAM, a 16 GiB virtual disk and its own SSH service. Only a source snapshot is
copied into it: host directories and the SSH agent are not shared.

```sh
python3 scripts/server.py create
python3 scripts/server.py status
python3 scripts/server.py verify
limactl shell --workdir=/home/pion-project pion-sender
```

The source snapshot includes the uncommitted Prague implementation. Copies of
the build/provisioning output, source hashes and verification logs are kept in
`results/raw/server-pion-sender/`. The guest project is `/home/pion-project`.
Prague and the VP8 sample are built inside the guest. `verify` runs the Linux
checks and a bounded Prague media call entirely inside that VM. Creating the
VM again does not overwrite an already prepared guest project.

For ordinary SSH use Lima's generated configuration:

```sh
ssh -F ~/.lima/pion-sender/ssh.config lima-pion-sender
```

The Ubuntu image is pinned by checksum in `testbed/pion-sender.lima.yaml`.
The template is for Lima 1.0+ on Apple Silicon. It uses the host-accessible
`vzNAT` virtual network, which does not attach the VM to the physical servers
switch. To stop the server when finished:

```sh
limactl stop pion-sender
```

## Placement for a physical experiment

| Sender placement | Path to the laptop | Requirement |
| --- | --- | --- |
| Local VM on this Mac | Virtual networking on the Mac | Development and Linux verification; does not traverse the physical bottleneck |
| Separate computer on the servers switch | Servers switch → lld-black4 → laptop | Matches the intended lab topology; use the existing two-host runner |
| Separate computer on the IETF network, outside EdgeRouterX | EdgeRouterX → servers switch → lld-black4 → laptop | Assigned compute host plus routing, NAT and signaling work described below |

In the supplied figure, `31.130.239.1` identifies the IETF-side gateway,
`31.130.239.198` labels EdgeRouterX's WAN side, and `192.168.1.1` labels its LAN
side. These labels do not supply an available server computer or login.
Running the media application directly on the shared router needs operator
agreement and can interfere with the forwarding measurements.

An IETF-side compute host is a possible sender location, but the current
application has the sender initiate HTTP signaling to the receiver's private
lab IP. To use an outside host, add receiver-initiated signaling to the public
sender and validate ICE/NAT connectivity, or arrange the necessary forwarding
with the operator. Ordinary SSH port forwarding carries TCP signaling, not the
UDP media and its outer-IP ECN metadata. TURN/tunnels can alter the ECN path and
would need separate validation. Also verify that the laptop's route to that
host uses lab Ethernet, not Wi-Fi, and correlate the actual flow with lld-black4.

With one Mac and one client-side Ethernet connection, creating additional
local processes, containers or VMs does not put a sender across the physical
bottleneck. A compute host on the other side, or a second isolated physical
connection for the VM, is still required for that experiment.

Lima documentation: [virtual networks](https://lima-vm.io/docs/config/network/),
[vzNAT](https://lima-vm.io/docs/config/network/vznat/), and
[bridged networking](https://lima-vm.io/docs/config/network/vmnet/).
