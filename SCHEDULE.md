# Run ViralBolly every evening at 9 PM (Windows)

The pipeline is already installed in `C:\workspaces\bollygossip`. A run takes about
20 minutes and needs no AI/LLM and no API keys. This sets Windows to run it by itself
every day at 9:00 PM.

## Option A: one command (recommended)

1. Press **Start**, type **PowerShell**, and open **Windows PowerShell** (no admin needed).
2. Paste this and press Enter:

```powershell
$action   = New-ScheduledTaskAction -Execute "C:\workspaces\bollygossip\run_daily.bat" -WorkingDirectory "C:\workspaces\bollygossip"
$trigger  = New-ScheduledTaskTrigger -Daily -At 9pm
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "ViralBolly Daily" -Action $action -Trigger $trigger -Settings $settings -Description "Daily Bollywood gossip shorts + long video"
```

What the settings do:
- **WakeToRun**: wakes the PC from sleep at 9 PM to run (a shut-down PC or closed lid on some laptops won't wake).
- **StartWhenAvailable**: if the PC was off at 9 PM, it runs as soon as you're back on.
- **2-hour limit**: stops a stuck run instead of letting it hang forever.

## Option B: by clicking

1. Start > type **Task Scheduler** > open it.
2. **Create Basic Task…** > Name: `ViralBolly Daily` > Next.
3. **Daily** > Next > Start time **9:00:00 PM**, recur every **1** day > Next.
4. **Start a program** > Next.
   - Program/script: `C:\workspaces\bollygossip\run_daily.bat`
   - Start in: `C:\workspaces\bollygossip`
5. Tick **Open the Properties dialog** > Finish.
6. In Properties:
   - **Conditions** tab: tick **Wake the computer to run this task**; untick **Start the task only if the computer is on AC power**.
   - **Settings** tab: tick **Run task as soon as possible after a scheduled start is missed**; set **Stop the task if it runs longer than** to **2 hours**.
7. OK.

## Check it works

Run it once right now instead of waiting for 9 PM:

```powershell
Start-ScheduledTask -TaskName "ViralBolly Daily"
```

Then after ~20 minutes look in `C:\workspaces\bollygossip\output\<today's date>\`:

| File | What it is |
|---|---|
| `short_1.mp4`, `short_2.mp4` | vertical shorts, up to 2 min each |
| `long.mp4` | landscape video, up to 20 min |
| `*_description.txt` | paste into YouTube: title on line 1, then the description |
| `long_pinned_comment.txt` | full credit links for the long video (post as a pinned comment) |
| `*_youtube.json` | sources, credits, links and chapter times as data |

Progress and errors are written to `C:\workspaces\bollygossip\output\run.log`.

Note: the date folder uses the UTC date. This PC's clock is on US Eastern time, so a
9 PM run lands in the *next* day's folder (9 PM EDT = 1 AM UTC the next day).

## Change or stop it

- Change the time: `Set-ScheduledTask -TaskName "ViralBolly Daily" -Trigger (New-ScheduledTaskTrigger -Daily -At 8pm)`
- Pause: `Disable-ScheduledTask -TaskName "ViralBolly Daily"` (resume with `Enable-ScheduledTask`)
- Remove: `Unregister-ScheduledTask -TaskName "ViralBolly Daily" -Confirm:$false`
- Last result: `Get-ScheduledTaskInfo -TaskName "ViralBolly Daily"` (`LastTaskResult` 0 = success)

## Settings

Everything else lives in `config.json` in the same folder: ffmpeg folder, music file
(`music\background.mp3`), how many shorts, their length, the long video's length,
YouTube channels, and names to block. Edit it with Notepad; the next run picks it up.

## Things to know

- The PC must be on (or asleep, with WakeToRun) and connected to the internet at 9 PM.
- Reddit currently refuses direct access from this network, so the pipeline reads
  Reddit's public RSS feed instead (no upvote counts; Reddit's own "top today" order).
- Items used on earlier days are remembered in `output\used_posts.json` and not repeated.
- Watch the videos before uploading: credits are shown, but credit isn't permission,
  and gossip can be wrong.
