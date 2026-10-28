# nature-7000 chain: tab tak runs karta rahe jab tak folder me 7000 images na ho jayein.
# Har run ek hi folder me save hota hai -> cross-run dedupe (seen_ids + ahash) kaam karta hai.
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

W ("chain start - abhi images: " + (Count-Images))

# ---- manifest reconcile ----
# Server crash ho to images disk pe reh jaati hain par manifest nahi banti ->
# agla run seq=old_rows leta aur purani files OVERWRITE kar deta + duplicate
# bante. Har start se pehle isliye manifest disk se sync karwate hain.
function Sync-Manifest {
    try {
        $out = & python "C:\Users\hp\wallpaper-tool\fix_manifest.py" 2>&1 |
               Where-Object { $_ -match 'manifest=|added=|missing nahi' }
        foreach ($o in $out) { W ("  sync: " + $o) }
    } catch { W ("  sync FAIL: " + $_) }
}
Sync-Manifest

$fails = 0   # lagatar server failures -> 5 baar tak retry, phir band

for ($run = 1; $run -le $MAXRUNS; $run++) {
    $have = Count-Images
    if ($have -ge $TARGET) { W ("TARGET mil gaya: $have/$TARGET"); break }

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

    W ("--- run " + $run + " (start, images abhi " + $have + ") ---")
    try {
        $r = Invoke-RestMethod -Uri "$API/api/start" -Method Post -Body $body `
                               -ContentType "application/json" -TimeoutSec 30
    } catch {
        W ("START FAIL (server band hai?): " + $_); break
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
            W ("status FAIL #$fails (server zinda hai?): " + $_.Exception.Message)
            if ($fails -ge 5) { W ("CHAIN ABORT - server band ho gaya. dobara start karo."); break }
            Start-Sleep -Seconds 20; continue
        }
        if ($s.state -eq "done")   { W ("run done -> saved " + $s.counts.saved); Sync-Manifest; break }
        if ($s.state -eq "error")  { W ("run ERROR: " + $s.error); break }
        # beech me progress note (har ~5 min)
        if (-not $script:tick) { $script:tick = 0 }
        $script:tick++
        if ($script:tick % 15 -eq 0) {
            W ("  " + $s.state + "  saved=" + $s.counts.saved + "/" + $s.counts.target)
        }
    }
    W ("folder abhi: " + (Count-Images) + " images")
}

W ("chain END - final: " + (Count-Images) + " images  ->  $FOLDER")
