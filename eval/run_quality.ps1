# Quality test on the format fixtures (eval/make_formats.py). Runs each task plain and through the
# /blindfold skill, then asks a judge whether the protected answer is at least as good. Requires:
# blindfold serve running, Claude Code signed in, ANTHROPIC_BASE_URL pointing at the proxy.
param([string]$Docs = "eval\out\formats\files", [string]$Out = "eval\out\quality", [string]$Tasks = "eval\tasks.json")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
New-Item -ItemType Directory -Force $Out | Out-Null
$skillRaw = Get-Content (Join-Path $PSScriptRoot "..\src\blindfold\skill\SKILL.md") -Raw -Encoding utf8
$skillBody = ($skillRaw -split "---", 3)[2].Replace('!`blindfold activate $0 ${CLAUDE_SESSION_ID}`', "")
$results = @()
# The runs work in a scratch copy of the fixtures without seeds.json, so neither run can read the answers.
$work = Join-Path ([IO.Path]::GetTempPath()) ("bf_quality_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
Get-ChildItem $Docs -File | Where-Object { $_.Name -ne "seeds.json" } | Copy-Item -Destination $work
$tools = "Bash,Read,Glob,Grep"

function Ask([string]$text, [string]$depth) {
    # One retry after a minute when the model service throttles: that is upstream load, not a blindfold result.
    # A protected run gets a fresh activated session ID per attempt (Claude Code refuses a reused ID).
    for ($try = 0; $try -lt 2; $try++) {
        $sid = ""
        if ($depth) {
            $sid = [guid]::NewGuid().ToString()
            blindfold activate $depth $sid | Out-Null
        }
        Push-Location $work
        try {
            $out = if ($sid) { ($text | claude -p --session-id $sid --allowedTools $tools) -join "`n" }
                   else { ($text | claude -p --allowedTools $tools) -join "`n" }
        } finally { Pop-Location }
        if ($out -notmatch "^\W*API Error") { return $out }
        Start-Sleep -Seconds 60
    }
    return $out
}

function Judge([string]$task, [string]$a, [string]$b) {
    $q = "Task: $task`n`nAnswer A:`n$a`n`nAnswer B:`n$b`n`n" +
         "Which answer serves the task better? Reply with exactly one word: A, B or TIE."
    Push-Location $work   # the judge may open the source document, never the answer key
    try { $reply = ($q | claude -p --allowedTools $tools) -join " " } finally { Pop-Location }
    $word = (($reply.Trim() -split "\s+")[0] -replace "[^\p{L}]", "").ToUpper()
    if ($word -in @("A", "B", "TIE")) { return $word }
    return "INVALID"
}

foreach ($t in (Get-Content $Tasks -Raw -Encoding utf8 | ConvertFrom-Json)) {
    $prompt = $t.prompt.Replace("{doc}", $t.doc)
    $depth = if ($t.strict) { "strict" } else { "normal" }
    $plain = Ask $prompt ""
    # Headless claude -p runs a "/blindfold ..." prompt as a local command with no model turn, so the
    # runner does what the skill does: activate a fresh session ID, then send the skill text before the task.
    $prot = Ask ("$skillBody".Replace('$0', $depth) + "`n`n" + $prompt) $depth
    $plainPath = Join-Path $Out "$($t.id).plain.txt"
    $protPath = Join-Path $Out "$($t.id).blindfold.txt"
    Set-Content -Encoding utf8 $plainPath $plain
    Set-Content -Encoding utf8 $protPath $prot
    # Judge both orders so position bias cannot decide; the protected answer passes unless the plain one wins.
    $first = Judge $prompt $prot $plain     # protected is A
    $second = Judge $prompt $plain $prot    # protected is B
    $verdict = if ($first -eq "INVALID" -or $second -eq "INVALID") { "INVALID" }
               elseif ($first -eq "B" -or $second -eq "A") { "NO" } else { "YES" }
    # Control: a second plain run judged against the first by the same rule. Its pass rate is the judge's
    # noise floor; the blindfold rate means something only relative to it.
    $plain2 = Ask $prompt ""
    $plain2Path = Join-Path $Out "$($t.id).plain2.txt"
    Set-Content -Encoding utf8 $plain2Path $plain2
    $c1 = Judge $prompt $plain2 $plain
    $c2 = Judge $prompt $plain $plain2
    $control = if ($c1 -eq "INVALID" -or $c2 -eq "INVALID") { "INVALID" }
               elseif ($c1 -eq "B" -or $c2 -eq "A") { "NO" } else { "YES" }
    $results += [pscustomobject]@{ id = $t.id; verdict = $verdict; judge_orders = "$first/$second"; control = $control;
                                   control_orders = "$c1/$c2"; plain = $plainPath; blindfold = $protPath; plain2 = $plain2Path }
}
ConvertTo-Json @($results) | Set-Content -Encoding utf8 (Join-Path $Out "verdicts.json")
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue   # a lingering claude process may still hold it
$valid = @($results | Where-Object { $_.verdict -ne "INVALID" })
$yes = @($valid | Where-Object { $_.verdict -eq "YES" }).Count
Write-Host "At least as good: $yes of $($valid.Count) valid ($($results.Count - $valid.Count) invalid)"
$cvalid = @($results | Where-Object { $_.control -ne "INVALID" })
$cyes = @($cvalid | Where-Object { $_.control -eq "YES" }).Count
Write-Host "Control (second plain run vs plain): $cyes of $($cvalid.Count) valid"
# Firm preferences (same winner in both orders) show the direction the strict rule above hides.
$firm = { param($o, $x) @($results | Where-Object { $_.$o -eq $x }).Count }
Write-Host ("Blindfold vs plain, firm wins: {0} blindfold, {1} plain" -f (& $firm judge_orders "A/B"), (& $firm judge_orders "B/A"))
Write-Host ("Plain2 vs plain, firm wins: {0} plain2, {1} plain" -f (& $firm control_orders "A/B"), (& $firm control_orders "B/A"))
