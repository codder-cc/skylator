# Deploying Skylator for long-horizon (weeks–months) autonomous runs

For an unattended run, both the agents and (recommended) the master should run as
auto-restarting OS services so they survive reboots, power loss, OOM kills, and crashes.
Combined with the durable stores, a restart resumes in-flight work with **zero loss**.

| Platform | Agent install | Auto-restart mechanism |
|---|---|---|
| Linux   | `skylator-agent.service` (+ `skylator-agent.env`) | systemd `Restart=always` |
| macOS   | `com.skylator.agent.plist` | launchd `KeepAlive` |
| Windows | `install-windows.ps1` (NSSM) | service `AppExit=Restart` |

Master (Linux): `../../deploy/skylator-master.service`.

## Why this is part of fault tolerance

- The agent writes every translation to `worker_data/worker_results.db` *before* delivery,
  so a kill mid-run loses at most one in-flight string.
- On relaunch the service restarts the process; the agent reads its durable manifest and
  **resumes** the unfinished assignment automatically (no operator action).
- The master preserves all in-flight assignments on boot and re-reconciles via pull.

## Autonomous-run checklist

1. Install the agent as a service on each worker, with `--model-path` set so the model
   loads at startup (the agent can then resume without waiting for the host).
2. Run the master as a service (recommended) or ensure it is reachable when you check in.
3. Leave it. Check progress anytime via the master UI or `GET /api/assignments`
   (per-agent funnel + liveness tier). Agents silent < the multi-day horizon keep their
   work; only longer silences are reassigned.
4. If a machine is gone for good, `POST /api/workers/<label>/abandon` to reassign its
   remaining strings immediately instead of waiting out the horizon.
