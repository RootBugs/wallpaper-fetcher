# nature-7000 chain: keeps launching runs until the folder holds 7000 images.
# Every run saves into the SAME folder -> cross-run dedupe (seen_ids + ahash) applies.
$ErrorActionPreference = "Stop"
$API   = "http://127.0.0.1:8765"
$FOLDER = "C:\Users\hp\Pictures\nature-7000"
$TARGET = 7000
$MAXRUNS = 6
$LOG = "C:\Users\hp\wallpaper-tool\nature-7000-chain.log"

function W($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    Add-Content -Path $LOG -Value $line -Encoding UTF8
    Write-Output $line
}
function Count-Images {
    if (-not (Test-Path $FOLDER)) { return 0 }
    return @(Get-ChildItem $FOLDER -File -ErrorAction SilentlyContinue |
             Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|webp)$' }).Count
}

W ("chain start - images right now: " + (Count-Images))

# ---- manifest reconcile ----
# If the server crashes, images stay on disk but the manifest is never written.
# The next run would then take seq = old_rows, OVERWRITE old files and create
# duplicates. So the manifest is re-synced from disk before every start.
function Sync-Manifest {
    try {
        $out = & python "C:\Users\hp\wallpaper-tool\fix_manifest.py" 2>&1 |
               Where-Object { $_ -match 'manifest=|added=|missing' }
        foreach ($o in $out) { W ("  sync: " + $o) }
    } catch { W ("  sync FAIL: " + $_) }
}
Sync-Manifest

$fails = 0   # consecutive server failures -> retry up to 5 times, then stop

for ($run = 1; $run -le $MAXRUNS; $run++) {
    $have = Count-Images
    if ($have -ge $TARGET) { W ("TARGET reached: $have/$TARGET"); break }

    $body = @{
        keywords   = "nature, landscape, aesthetic"
        count      = 2000
        colour     = "any"
        style      = "any"
        source     = "all"
        orientation= "any"
        min_res    = 1920
        category   = "general"
        smart      = $true
        newfolder  = $false
        folder     = "nature-7000"
    } | ConvertTo-Json

    W ("--- run " + $run + " (starting, images so far " + $have + ") ---")
    try {
        $r = Invoke-RestMethod -Uri "$API/api/start" -Method Post -Body $body `
                               -ContentType "application/json" -TimeoutSec 30
    } catch {
        W ("START FAIL (is the server down?): " + $_); break
    }
    $job = $r.job
    W ("job = " + $job)

    # poll until done/error
    while ($true) {
        Start-Sleep -Seconds 20
        try {
            $s = Invoke-RestMethod -Uri "$API/api/status?job=$job" -TimeoutSec 15
            $fails = 0
        } catch {
            $fails++
            W ("status FAIL #$fails (is the server alive?): " + $_.Exception.Message)
            if ($fails -ge 5) { W ("CHAIN ABORT - the server went down. Restart it and run again."); break }
            Start-Sleep -Seconds 20; continue
        }
        if ($s.state -eq "done")   { W ("run done -> saved " + $s.counts.saved); Sync-Manifest; break }
        if ($s.state -eq "error")  { W ("run ERROR: " + $s.error); break }
        # progress note every ~5 minutes
        if (-not $script:tick) { $script:tick = 0 }
        $script:tick++
        if ($script:tick % 15 -eq 0) {
            W ("  " + $s.state + "  saved=" + $s.counts.saved + "/" + $s.counts.target)
        }
    }
    W ("folder now: " + (Count-Images) + " images")
}

W ("chain END - final: " + (Count-Images) + " images  ->  $FOLDER")
