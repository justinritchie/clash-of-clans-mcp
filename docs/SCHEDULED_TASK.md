# Scheduled Snapshot Task

The snapshot store only captures wars that exist when you snapshot. To avoid
gaps, run snapshots on a schedule.

## Option 1: Cowork scheduled Claude task (recommended)

Cowork supports scheduled prompts. Create one that runs every 2 days:

**Prompt**:
> Run `clash_snapshot_war` to capture any new war data, then run `clash_snapshot_status` and report any gaps. Brief — just confirm what was snapshotted and call out any gaps that need attention.

**Cadence**: every 2 days at a quiet hour (e.g., 6am).

This catches both regular war days (one new war every ~48 hours when actively warring) and CWL rounds (one per day during the first ~10 days of each month).

## Option 2: Manual after each war

```bash
cd /path/to/clash-of-clans-mcp
python snapshot_war.py
```

## Option 3: macOS LaunchAgent (background, no Cowork needed)

Create `~/Library/LaunchAgents/com.coc-mcp.snapshot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.coc-mcp.snapshot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOU/path/to/clash-of-clans-mcp/snapshot_war.py</string>
    </array>
    <key>StartInterval</key>
    <integer>172800</integer>  <!-- 2 days in seconds -->
    <key>StandardOutPath</key>
    <string>/tmp/coc-snapshot.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/coc-snapshot.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>COC_API_TOKEN</key>
        <string>eyJ0eXAi...your token...</string>
        <key>COC_DEFAULT_CLAN_TAG</key>
        <string>#YV9JRULU</string>
    </dict>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.coc-mcp.snapshot.plist
```

## Why every 2 days

Regular wars run ~48 hours each (24h prep + 24h war). If you snapshot every 2
days, you'll catch each war once before the next one starts.

If maintenance / outages cause you to miss a snapshot, the war is unrecoverable
from the API — but `clash_snapshot_status` will flag the gap so you know.

## Reconciliation

Run `clash_snapshot_status` (or check the output of `snapshot_war.py`) to see
gaps between what's in the warlog and what's been snapshotted. Gaps are
unrecoverable but visible.
