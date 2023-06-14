# Multi-ping

A hacky text-mode multi-host ping tool written in Python with curses. Pings
multiple target at a fixed interface and shows latency and timeouts with fancy
colors.

## Configuration

### Target hosts

A TOML file `mping.toml` contains the target hosts. Each host must be in the
`hosts` section with a `alias = address` entry or an `alias.address = address`
entry. The address can be an IP address or a host name.

```toml
[hosts]
my-host-alias = "1.2.3.4"
my-other-host = "somehost.example.com"
```

```toml
[hosts.my-host-alias]
address = "1.2.3.4"
```

### Ping interval

The ping interval and timeout can be set using the `interval` option in the
`mping` section:

```toml
[mping]
interval = 2.0
timeout = 5.0
```

### Timeout log

`mping.py` will log timeouts per `interval` to a text file if the `log_timeouts`
option is set:

```toml
[mping]
log_timeouts = "timeouts.log"
```