# network-monitor

A Bitcoin network monitor

## Local dev

1. Install VirtualBox and Vagrant for your platform
    - This may require enabling CPU Virtualization within your bios.
    - We don't use Docker for local development since the infrastructure itself makes
      use of Docker containers, and recursive Docker use gets tricky.
1. Add the following entry to your `/etc/hosts` file:
    ```
    # bmon
    192.168.56.2  bmon-server
    192.168.56.3  bmon-b1
    192.168.56.4  bmon-b2
    ```
1. If you don't have your SSH pubkey at `~/.ssh/id_rsa.pub`, adjust the corresponding
  configuration in `Vagrantfile`.
1. Run `vagrant up` to provision the VMs.
1. Run `pip install -e .` to install `bmon` infrastructure tooling on your host
   machine. It is recommended to do this in a non-root virtualenv.
1. Run `./deploy/dev.py provision` to install bmon configuration and dependencies on
  each host.
1. Browse to `http://bmon-server:3000` to access Grafana; use the default admin
  credentials `admin`/`admin`.
1. Import a sample dashboard using `Dashboards -> Import -> Import via panel json` with
  the contents of `etc/sample-grafana-infra-dashboard.json`.

[Supervisor](http://supervisord.org/) is used to manage the processes on each host
under the `root` account; you can perform management by running `ssh root@<host>` and
then using the `supervisorctl` command.

In flagrant violation of posix standards, all logs and program configuration are
available under `/bmon` on each host.


## Design

Bmon consists of two machine types: one server and many nodes. The nodes run bitcoind,
and provide information to the server, which collects and synthesizes all the data
necessary. The server also provides views on the data, including log exploration,
metric presentation, and other high-level insights (TBD).

The bmon server runs

- loki, for log aggregation
- alertmanager, for alerts
- grafana, for presenting logs and metrics
- prometheus, for aggregating metrics
- [tbd] bmon_collector, which aggregates insights

Each bmon node (the analogue of a bitcoind node) runs

- bitcoind, which runs bitcoin
- promtail, which pushes logs into loki (on the server)
- node_exporter, which offers system metrics for scraping by prometheus
- bmon_exporter, which pushes interesting high-level data into 

```mermaid
flowchart TD
  subgraph node
      node_exporter
      bmon_exporter
  end
  subgraph server
      loki
      grafana
      alertmanager
      prometheus
      loki --> grafana
      prometheus --> grafana
      bmon_exporter --> bmon_collector
  end
  subgraph node
    promtail
    promtail --> loki
    bitcoind --> /bmon/logs/bitcoin.log
    /bmon/logs/bitcoin.log --> promtail
    bitcoind --> bmon_exporter
    node_exporter --> prometheus
    prometheus --> alertmanager
  end
```

For simplification, all servers participate in a single wireguard network.

### Node versions

- One for each major release
- One for current RC
- Maintain 3 rotating versions of master, staggered backwards by
  - 1 week
  - 4 weeks
  - 16 weeks

### Uses

- [ ] For a given block, determine when it was seen by each node. Present variance.
    Alert on anomalous variance.

- [ ] For a given transaction, determine when it was seen by each node. Present
    variance. Alert on anomalous variance.

- [ ] "Selfish mining" detector: alert on multiple blocks in rapid succession that
    cause a reorg.

### Notify on

- [ ] mempool empty
- [ ] inflation (rolling sum of UTXO amounts + (block_created_amt - block_destroyed_amt) > supply_at_height)
- [ ] tip older than 90 minutes
- [ ] transactions rejected from mempool
- [ ] bad blocks
- [ ] reorgs

### Measurements

- [ ] block reception time per node
- [ ] txn reception time per node
- [ ] reorg count (number of unused tips?)
- [ ] usual system metrics: memory usage, disk usage, CPU load, etc.

### Comparison across nodes

- [ ] mempool contents 
- [ ] getblocktemplate contents (do they differ at all?)
- [ ] block processing time (per logs)
- [ ] block reception time diff
- [ ] txn reception time diff

### Features

- [ ] logs sent to a centralized log explorer (Loki-Grafana)
