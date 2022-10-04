# bmon

A Bitcoin network monitor

## Local dev

1. Ensure you have Python 3.10+, Docker, and docker-compose on your host.
    - `pip install docker-compose`
1. Install the local infrastructure tools:
    - `pip install -e ./infra`
1. Build local config tree: `bmon-config`
1. Bring docker-compose up: `docker-compose up [-d]`
1. Browse to `http://localhost:3000` to access Grafana; use the default admin
  credentials `admin`/`admin`. You should see a nice little sample dashboard
  displaying bitcoind logs etc.


## Running tests

1. `./dev test`
1. Try generating a block locally:
    - `docker-compose up -d`
    - In one terminal: `./dev watchlogs`
    - In another: `./dev generateblock`


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

## How are hosts configured?

All known participants in bmon are listed in `./infra/hosts.yml`. This file is parsed
by `./infra/bmon_infra/infra.py` (which gets installed as the `bmon-infra`), which
then configures each host over SSH (using [fscm](https://github.com/jamesob/fscm),
which itself uses mitogen, a Python library that basically facilitates remote execution of
Python code over an SSH connection).

During provisioning, a copy of the `bmon` repo is cloned on each host at `~/bmon`,
and then `bmon-config` (`./infra/bmon_infra/config.py`) is run to generate a `.env`
file with all configuration and secrets based on the host's entry in `hosts.yml`.

The `.env` file is read in by docker-compose and used to set various parameters of the
container runtimes. The docker-compose lifecycle is managed by systemd on each host; a
user-level systemd unit is installed by the `bmon-infra` command.


## How is wireguard used?

Since monitored hosts will live on different networks, wireguard is used to create a
flat networking topology so that all hosts can be easily reached by the central bmon
server, which aggregates measurements across each host.

To add a host, file an issue here and I'll give a wireguard config to use.

Wireguard is also used to simulate geographical dispersion of the monitored nodes. A
VPN provider gives us Wireguard configurations for diverse networks, which we then use
on certain monitored bitcoind hosts.


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


### Misc.

#### Resizing existing vagrant disk

sudo cfdisk /dev/sda
sudo resize2fs -p -F /dev/sda1
