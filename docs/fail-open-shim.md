# The fail-open shim

## Why you want one

Intercepting the device's traffic puts Home Assistant between it and PumpSpy.
That is the whole point -- but it also means an HA outage stops the device
reaching the vendor at all, and the device does not buffer: it retries three
times about 110 ms apart and then drops the event permanently. If you rely on
PumpSpy's own cloud alerts as a flood safety net, a routine HA restart quietly
punches a hole in it.

It is worse than the downtime suggests. Measured on a real install, the device
took **more than seven minutes** to resume reporting after a 
Home Assistant restart -- once refused a connection, it backs off. So a
90-second restart can cost the better part of ten minutes of vendor alerting.

The shim removes that entirely. nginx takes the device's connection, hands the
request to Home Assistant, and forwards it straight to the vendor if Home
Assistant does not answer. On the same install, an HA restart measured at 71
seconds cost **zero** vendor deliveries: the request that landed inside the
window was served by PumpSpy in 0.25 s and the device never saw a failure, so it
never backed off. Telemetry resumed on its normal two-minute beat.

Local monitoring still pauses while HA is down. That is the right way round.

## Where to run it

The shim has to survive the thing it is protecting you from, so it must not
share a failure domain with Home Assistant. In rough order of how likely you are
to already have the pieces:

| Your setup | Run nginx as | Survives |
|---|---|---|
| HA in Docker or Compose, incl. most NAS installs | another service beside it | HA container restarts, HA updates |
| HA OS on a Pi or bare metal, nothing else always-on | a HAOS add-on | HA **core** restarts, which is the common outage |
| HA as a VM or LXC under a hypervisor | a sibling container on the same host | core restarts, VM reboots, HAOS updates |
| A capable router (OPNsense, OpenWrt) | a service on the router | everything above, plus the HA machine being off |

The add-on is the weakest of these -- it dies when the whole HA OS machine does
-- but if that machine is the only always-on thing you own, it still covers the
outage you actually hit week to week. If HA runs as a guest under a hypervisor,
a sibling container beats the add-on outright: it survives strictly more, and
the host going down would have taken HA with it anyway, so it adds no new
failure mode.

Wherever it runs, that machine becomes the device's front door. See
"When the shim itself dies" below.

## Installing it

1. Copy [`shim/pumpspy-shim.conf`](../shim/pumpspy-shim.conf) into your nginx
   configuration -- on Debian and friends, `/etc/nginx/conf.d/`.
2. Replace `HOME_ASSISTANT` with the address Home Assistant listens on. If nginx
   runs on a *different* machine, that is HA's LAN address and the port from the
   integration's settings. If nginx runs on the *same* machine (the add-on
   case), nginx must take 8081 and the integration has to move: change the port
   in the integration's options, then point the upstream at the new one.
3. Remove any default site that would also bind the port, then
   `nginx -t && systemctl reload nginx`.
4. Point your DNS rewrite or NAT redirect at nginx instead of at Home
   Assistant, and make sure nginx's own path to `www.pumpspy.com` is *not*
   caught by that redirect -- otherwise the fallback loops back into itself.
   Scoping the redirect to the device's source address is the simplest way.

Then reload nginx nightly, e.g. from `/etc/cron.d/`:

```
23 4 * * * root /usr/sbin/nginx -t -q && /bin/systemctl reload nginx
```

nginx resolves the vendor's addresses only when it starts or reloads -- and
`www.pumpspy.com` answers with eight of them, which will not stay the same
forever. Without a periodic reload the fallback path can strand itself on stale
addresses months later, and you would find out during an outage.

## When the shim itself dies

The shim covers every failure except its own. If your redirect points at nginx
and nginx is not there, the device is cut off from both sides -- the exact
outcome this is meant to prevent.

The config exposes `/shim-health`, which answers `ok` without touching either
upstream. Poll it from wherever the redirect is configured, and withdraw the
redirect when it stops answering: the device then talks to the vendor directly,
by itself, until the shim is back. A once-a-minute check is enough. This repo's
reference install does it from the same cron job that re-applies the redirect
after the router reboots.

## Verifying it

Do not trust a config test for this. The failure modes all look healthy.

1. **Normal path.** Watch the access log (`/var/log/nginx/pumpspy.log`). Within
   a couple of minutes you should see the device's `POST  /bbs_json` and
   `POST  /pings`, plus `GET /new_firmware/...` and `GET /bbs_parameters/...`,
   all with your Home Assistant address as the upstream and `device_got=200`.
   Both request kinds matter: telemetry has a body and the GETs do not, and it
   is the GETs that a default nginx breaks.
2. **Outage path.** Stop Home Assistant. The next requests must show a *vendor*
   address as the second upstream and still `device_got=200`, like this:

   ```
   POST  /pings -> 10.0.0.5:8081, 206.80.104.221:8081 upstream=502, 200 in 0.000, 0.106s, device_got=200
   ```

   A real vendor address and a real response -- not a synthetic 200 from nginx.
3. **Recovery.** Start Home Assistant. Telemetry should return to it on the
   device's normal beat, and your entities should update without a long gap.

## What it does not do

While the shim is falling back, requests go to the vendor unfiltered, so
firmware quarantine is not applied for the duration. Quarantine was already
best-effort -- interception is presence-based, and any window without the
redirect is a window where the device polls the vendor directly. This adds one
more such window, deliberately: not delivering a flood alert is the worse
failure.
