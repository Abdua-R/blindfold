# Re-judges answers already saved by run_quality.ps1 (plain and blindfold text files), without re-running the tasks.
# Same judge as run_quality.ps1: A/B/TIE in both orders, in a scratch copy of the fixtures without seeds.json.
param([string]$Docs = "eval\out\formats\files", [string]$Out = "eval\out\quality", [string]$Tasks = "eval\tasks.json")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$work = Join-Path ([IO.Path]::GetTempPath()) ("bf_judge_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
Get-ChildItem $Docs -File | Where-Object { $_.Name -ne "seeds.json" } | Copy-Item -Destination $work
$tools = "Bash,Read,Glob,Grep"

function Judge([string]$task, [string]$a, [string]$b) {
    $q = "Task: $task`n`nAnswer A:`n$a`n`nAnswer B:`n$b`n`n" +
         "Which answer serves the task better? Reply with exactly one word: A, B or TIE."
    Push-Location $work
    try { $reply = ($q | claude -p --allowedTools $tools) -join " " } finally { Pop-Location }
    $word = (($reply.Trim() -split "\s+")[0] -replace "[^\p{L}]", "").ToUpper()
    if ($word -in @("A", "B", "TIE")) { return $word }
    return "INVALID"
}

$results = @()
foreach ($t in (Get-Content $Tasks -Raw -Encoding utf8 | ConvertFrom-Json)) {
    $prompt = $t.prompt.Replace("{doc}", $t.doc)
    $plainPath = Join-Path $Out "$($t.id).plain.txt"
    $protPath = Join-Path $Out "$($t.id).blindfold.txt"
    $plain = Get-Content $plainPath -Raw -Encoding utf8
    $prot = Get-Content $protPath -Raw -Encoding utf8
    $first = Judge $prompt $prot $plain
    $second = Judge $prompt $plain $prot
    $verdict = if ($first -eq "INVALID" -or $second -eq "INVALID") { "INVALID" }
               elseif ($first -eq "B" -or $second -eq "A") { "NO" } else { "YES" }
    $results += [pscustomobject]@{ id = $t.id; verdict = $verdict; judge_orders = "$first/$second"; plain = $plainPath; blindfold = $protPath }
}
ConvertTo-Json @($results) | Set-Content -Encoding utf8 (Join-Path $Out "verdicts.json")
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
$valid = @($results | Where-Object { $_.verdict -ne "INVALID" })
$yes = @($valid | Where-Object { $_.verdict -eq "YES" }).Count
Write-Host "At least as good: $yes of $($valid.Count) valid ($($results.Count - $valid.Count) invalid)"
