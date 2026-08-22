# Setting up pumpspy-local

## What you need

- Home Assistant — any install type: OS, Supervised, Container or Core.
- A working PumpSpy or PitBoss+ monitor, already reporting to the vendor.
- **A way to make the device's traffic arrive at Home Assistant.** This is the
  only real prerequisite, and the only step that depends on your network. See
  step 3; read it before you start, because not every router can do it.

There is no MQTT broker, no add-on and no separate service. The integration
listens for the device itself.

## 1. Install the integration

Through [HACS](https://hacs.xyz/): add this repository as a custom repository of
type *Integration*, install it, and restart Home Assistant.

By hand: copy `custom_components/pumpspy_local/` into your Home Assistant
`config/custom_components/` directory and restart.

## 2. Add it

**Settings → Devices & Services → Add Integration → pumpspy-local.**

| Field | Default | What it is |
|---|---|---|
| Listen port | `8081` | The port the device reports to. Leave it unless you know otherwise -- everything this device sends goes to 8081, including firmware polls. |
| Forward to | `http://www.pumpspy.com:8081` | Where your device's traffic is relayed so the vendor's app and alerting keep working. |
| Look up the vendor using | `1.1.1.1` | A resolver your redirect is *not* installed in. If you redirect by DNS, a normal lookup for the vendor would send Home Assistant back to itself. |
| Vendor address (optional) | blank | Set this only if the lookup above cannot work — outbound DNS blocked, for example. Given an address, no lookup happens at all. |
| Gallons per second | `1.0` | Used to estimate gallons from run duration. The device does not report volume, so every gallons figure is an estimate. `1.0` is the vendor's own nominal figure. |
| Firmware updates | Observe | *Observe* watches and tells you. *Hold for my approval* keeps an offered update back until you approve it. See the caveat in step 5. |
| Check for firmware every | `24` hours | Your device asks roughly every 13 seconds; this is how often that question is actually passed to the vendor. The device still gets an answer every time. |

**No devices appear yet, and that is expected.** Entities are created when a
device first reports — there is nothing to configure per device.

## 3. Send the device's traffic to Home Assistant

The principle, whatever your network: **every connection the device makes to
`www.pumpspy.com:8081` has to land on Home Assistant instead, while Home
Assistant itself can still reach the real vendor.**

Three ways, best first.

### Option A — redirect the traffic (recommended)

A destination-NAT rule on your router: traffic *from the device* to port 8081
goes to Home Assistant instead.

This is the reliable option because it does not care what the device believes.
It takes effect on the device's very next connection, with no reboot, and it
keeps working if the device's DNS is cached, hardcoded, or ignores your server
entirely.

Two things to get right:

- **Scope the rule to the device's own address.** A blanket rule would also
  catch Home Assistant's forwarded traffic and loop it back into itself.
- **If the device and Home Assistant are on the same subnet, you also need
  source NAT** on the redirected traffic. Without it, Home Assistant replies
  directly to the device, the device sees a reply from an address it never
  contacted, and drops it.

A worked example is in the appendix.

### Option B — a DNS rewrite

One rewrite in AdGuard Home, Pi-hole, or your router's DNS: `www.pumpspy.com`
answers with Home Assistant's address.

Simpler, and portable across routers — but it is **not reliable on these
devices.** The monitor observed here resolves once and then caches the answer
indefinitely, so the rewrite does nothing until the device restarts, and it is
battery-backed, so it does not restart just because you cut mains power. If you
take this route, plan on power-cycling the device (including its battery) and
verify entities actually appear before you trust it.

With this option, set **Look up the vendor using** to a resolver that does not
have the rewrite, or the relay will be redirected back into Home Assistant.

### Option C — an isolated VLAN with a firewall allow-list

Put the device on its own VLAN and allow it to reach only Home Assistant. This
is the only airtight option: it also blocks the device from reaching the vendor
directly, so firmware quarantine cannot be bypassed.

**It fails closed, and that is a real cost.** If Home Assistant is down, the
device cannot reach anyone, so the vendor's own flood alerting goes down with
it. If you rely on those alerts, this trades a genuine safety net for protection
against a hypothetical firmware push. That is why it is documented here rather
than recommended.

## 4. Check that it worked

- Within about two minutes, entities should appear — telemetry arrives on a
  two-minute cadence.
- **Check the vendor's own app still shows current data.** That is the proof the
  relay is working, not just the interception.
- `ac_power`, `high_water` and `motor_fail` will read *unknown*, and that is
  correct: the device only sends them when they change, which on a healthy
  install may be never. Never-reported is not the same as confirmed-fine.
- If the primary pump has not run, its run entities stay empty. Nothing is
  wrong; a pit that stays dry simply has nothing to report.

## 5. Keep vendor alerting alive when Home Assistant is down

Redirecting the device's traffic makes the vendor's alerting depend on Home
Assistant, which it never did before. The device does not buffer: it retries
three times and then drops the event for good. Worse, once refused it backs off
— measured here, a restart cost more than seven minutes of vendor delivery.

If you rely on the vendor's alerts, put a fail-open shim in front. It is a
small nginx config, it is tested, and on the same install it took an HA restart
from seven minutes of lost delivery to zero: **[the fail-open
shim](fail-open-shim.md)**.

The same page covers the one caveat on firmware quarantine: while the shim is
falling back to the vendor, firmware polls are not filtered. Quarantine is
best-effort by nature — any window where the redirect is not in force is a
window where the device can reach the vendor directly.

## 6. Know *why* the monitoring went quiet

Every part of this chain fails silently, and a stale entity looks exactly like a
calm sump. So it is worth alerting on silence — but the alert is only useful if
it names the right cause, and there are two very different ones.

The integration creates **`binary_sensor.pumpspy_local_vendor_reachable`**, which
says whether the requests being forwarded are actually reaching PumpSpy. It goes
*off* after four consecutive failed forwards and back *on* after two consecutive
successes; those numbers are measured, not chosen, and they keep the routine
hiccup (the vendor drops roughly one request in ten while perfectly healthy) from
flipping it. It reads *unknown* until something has been forwarded at all.

That matters because when PumpSpy's servers stop answering, the device gives up
and stops reporting to anyone — which from here is indistinguishable from a dead
redirect, unless you know the vendor was already failing. Attributes on the same
entity carry `last_delivery` (when a message last got through), the current
`consecutive_failures` run, and the `last_error`.

An automation that tells the three cases apart:

```yaml
alias: Sump monitoring has gone quiet
mode: single
triggers:
  - trigger: template
    id: gone_quiet
    value_template: >-
      {% set ns = namespace(newest=0) %}
      {% for s in states.sensor if s.object_id.startswith('pumpspy_') %}
      {% set ns.newest = [ns.newest, s.last_reported.timestamp()] | max %}
      {% endfor %}
      {{ ns.newest > 0 and (now().timestamp() - ns.newest) > 1800 }}
  - trigger: template
    id: recovered
    value_template: >-
      {% set ns = namespace(newest=0) %}
      {% for s in states.sensor if s.object_id.startswith('pumpspy_') %}
      {% set ns.newest = [ns.newest, s.last_reported.timestamp()] | max %}
      {% endfor %}
      {{ ns.newest > 0 and (now().timestamp() - ns.newest) < 300 }}
actions:
  - variables:
      vendor: binary_sensor.pumpspy_local_vendor_reachable
  - choose:
      # PumpSpy is down. Nothing here is broken and nothing needs doing --
      # except knowing that their alerting is off until they come back.
      - conditions:
          - condition: trigger
            id: gone_quiet
          - "{{ states(vendor) == 'off' }}"
        sequence:
          - action: notify.mobile_app_yourphone
            data:
              title: PumpSpy is down, your monitoring is fine
              message: >-
                No data for 30 minutes. The last message we delivered to PumpSpy
                was {{ relative_time(as_datetime(state_attr(vendor, 'last_delivery'))) }}
                ago, so this is their outage: the device stops reporting to
                anyone when it cannot reach them. The pump is unaffected, but
                PumpSpy's own alerts are off until they return.
      # We can reach PumpSpy but the device has stopped talking to us.
      - conditions:
          - condition: trigger
            id: gone_quiet
          - "{{ states(vendor) == 'on' }}"
        sequence:
          - action: notify.mobile_app_yourphone
            data:
              title: Sump monitoring has gone quiet
              message: >-
                No data from the device in 30 minutes, and PumpSpy is reachable
                from here -- so this is the device itself or the redirect, not
                their servers. The pump is unaffected either way.
      # Nothing has been forwarded at all, so there is nothing to go on.
      - conditions:
          - condition: trigger
            id: gone_quiet
        sequence:
          - action: notify.mobile_app_yourphone
            data:
              title: Sump monitoring has gone quiet
              message: >-
                No data from the device in 30 minutes, and nothing has been
                forwarded since Home Assistant started, so the cause is unknown.
                The pump is unaffected.
      - conditions:
          - condition: trigger
            id: recovered
        sequence:
          - action: notify.mobile_app_yourphone
            data:
              title: Sump monitoring is back
              message: The device is reporting again.
```

Two things to adjust: `notify.mobile_app_yourphone` is whatever your notifier is
called, and the entity id above assumes the service device still has its default
name — rename that device and the entity id follows it.

The 30-minute threshold is deliberately loose. Telemetry arrives every two
minutes, but after a Home Assistant restart the device took over seven minutes
to resume, so anything tight cries wolf on every restart.

### When the vendor is unreachable

The device does not keep reporting into the void. If the vendor's API stops
answering, it tolerates the failures for a few minutes, then decides its token
has gone stale and stops sending telemetry until something issues it a new one.
Measured during a real outage: nine minutes from the first failure to the first
re-authentication, and then no telemetry at all for as long as the vendor
stayed down.

So while the vendor is judged unreachable, this integration answers that
re-authentication itself rather than relaying a failure, and the device carries
on reporting locally. Nothing is sent to the vendor and no vendor credential is
used -- it is your device, on your network, asking this machine a question.

The token it is given is not one the vendor issued, so when the vendor comes
back it will be rejected, and the device will re-authenticate for real within a
few minutes. `binary_sensor.pumpspy_local_local_token_issued` is on while the
device is carrying a locally issued token, which is what explains a short burst
of rejections at recovery.

A vendor that is answering is never second-guessed: if it rejects the device's
credentials while it is otherwise healthy, that rejection is passed straight
through, because a real account problem should be visible rather than hidden.

## Appendix — worked example: UniFi Dream Machine

**This is one router's syntax, not a requirement.** UniFi has no interface for
this, so the rules are applied over SSH. Adapt the idea, not the commands.

Replace `DEVICE_IP` with your monitor's address and `TARGET_IP` with whatever
the device should reach — Home Assistant, or your shim if you set one up.

```sh
DEV=DEVICE_IP
TARGET=TARGET_IP
PORT=8081

# Everything the device sends to 8081 goes to TARGET instead.
iptables -t nat -I PREROUTING 1 -i br0 -s $DEV -p tcp --dport $PORT \
    -j DNAT --to-destination $TARGET:$PORT

# Needed because the device and the target share one flat LAN: without it the
# reply comes straight back from TARGET, from an address the device never
# contacted, and is dropped.
iptables -t nat -I POSTROUTING 1 -s $DEV -d $TARGET -p tcp --dport $PORT \
    -j MASQUERADE
```

Note the rule is scoped with `-s $DEV`. That is what keeps the relay's own
traffic to the vendor out of the redirect.

**UniFi OS discards custom iptables rules on reboot**, silently — and a device
whose redirect has vanished goes back to talking to the vendor directly, with
nothing on the dashboard to say so. So the rules need re-applying. A one-minute
cron job on any always-on machine that can SSH to the router is enough, as long
as it is idempotent and only logs when it actually had to restore something.
If you run the shim, have that same job poll its `/shim-health` endpoint and
*remove* the rules when it stops answering, so a dead shim sends the device back
to the vendor instead of into a hole.
