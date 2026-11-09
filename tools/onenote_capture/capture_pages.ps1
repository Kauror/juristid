# Read-only desktop OneNote capture of chosen pages (Juristid, docs/adr/0204 + 0205).
#
# Run on the capture workstation, with OneNote open and synced (RUNBOOK.md).
# Pages come from the capture-request file (manage.py onenote_capture_import worklist --out)
# or, for the section-by-section process, from one named section.
#
# Allowed OneNote calls, and only these: GetHierarchy, GetHyperlinkToObject, GetPageContent
# (piBasic), NavigateTo, Windows.CurrentWindow.CurrentPageId (read). NEVER UpdatePageContent,
# UpdateHierarchy, DeleteHierarchy, CreateNewPage, OpenHierarchy, Publish, MergeFiles, SyncHierarchy.
#
# Completeness rule: a page is complete only with 0 placeholders and every file copied.
# "Stable for two polls" is not completeness. NavigateTo is used only while placeholders or
# uncached files remain. Dates are taken by the sealer from the pre-navigation read.
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
param(
  [string]$Request = '',
  [string]$Section = '',
  [string]$Notebook = '',
  [Parameter(Mandatory = $true)][string]$OutDir,
  [int]$MaxFiles = 100000,
  [long]$MaxTotal = 2147483648,
  [long]$MaxFile = 209715200,
  [int]$MaxPolls = 30
)
$ErrorActionPreference = 'Stop'
if (($Request -eq '') -eq ($Section -eq '')) { throw 'Give exactly one of -Request FILE or -Section NAME.' }
if (Test-Path -LiteralPath $OutDir) {
  if (@(Get-ChildItem -LiteralPath $OutDir -Force).Count -gt 0) { throw "Refusing: $OutDir is not empty." }
} else { New-Item -ItemType Directory -Force $OutDir | Out-Null }

$app = New-Object -ComObject OneNote.Application
$ns = 'http://schemas.microsoft.com/office/onenote/2013/onenote'

# OneNote answers RPC_E_CALL_REJECTED / RPC_E_SERVERCALL_RETRYLATER while busy (sync, UI).
# Retry those two only, a bounded number of times; anything else fails the run.
$script:busyRetries = 0
function Invoke-Busy([scriptblock]$call) {
  for ($i = 0; $i -lt 8; $i++) {
    try { return & $call } catch {
      $h = $_.Exception.HResult; if ($_.Exception.InnerException) { $h = $_.Exception.InnerException.HResult }
      if ($h -ne -2147418111 -and $h -ne -2147417846) { throw }
      $script:busyRetries++; Start-Sleep -Seconds ([Math]::Min(60, 5 * [Math]::Pow(2, $i)))
    }
  }
  throw 'OneNote stayed busy'
}
function Utf8([string]$path, [string]$text) { [System.IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false)) }
# A function returning an XmlNamespaceManager must wrap it: PowerShell unrolls any IEnumerable
# it returns (the manager enumerates its prefixes), and SelectNodes then gets an array.
function Nsm([xml]$d) { $m = New-Object System.Xml.XmlNamespaceManager($d.NameTable); $m.AddNamespace('one', $ns); return ,$m }
function Read-Page([string]$id) { $x = ''; Invoke-Busy { $app.GetPageContent($id, [ref]$x, 0) } | Out-Null; return $x }
function Read-Hierarchy() { $x = ''; Invoke-Busy { $app.GetHierarchy('', 4, [ref]$x) } | Out-Null; return $x }
function Page-Link([string]$id) { $x = ''; Invoke-Busy { $app.GetHyperlinkToObject($id, '', [ref]$x) } | Out-Null; return $x }
# An empty one:OE, or one holding only its list marker, may hide a file not yet fetched.
function Empty-OEs([xml]$d) { return @($d.SelectNodes('//one:OE[not(*)] | //one:OE[one:List and count(*[local-name()!=''OEChildren''])=1]', (Nsm $d))) }
function Files([xml]$d) { return @($d.SelectNodes('//one:InsertedFile', (Nsm $d))) }
function Uncached([xml]$d) { return @(Files $d | Where-Object { -not ($_.pathCache -and (Test-Path -LiteralPath $_.pathCache)) }).Count }
function Page-Guid([string]$link) {
  $m = [regex]::Match($link, 'page-id=\{?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}?')
  if ($m.Success) { return $m.Groups[1].Value.ToUpperInvariant() } else { return '' }
}

$log = New-Object System.Collections.Generic.List[object]
$originalPage = ''
try { $originalPage = $app.Windows.CurrentWindow.CurrentPageId } catch { $originalPage = '' }
$log.Add([pscustomobject]@{ event = 'start'; at = [DateTime]::UtcNow.ToString('o'); mode = $(if ($Request) { 'request' } else { 'section' }) })

# 1. The hierarchy before, and the pages to read.
$before = Read-Hierarchy
Utf8 (Join-Path $OutDir 'hierarchy-before.xml') $before
[xml]$h = $before; $hm = Nsm $h
$wanted = @{}
if ($Request) {
  $req = Get-Content -Raw -Encoding UTF8 -LiteralPath $Request | ConvertFrom-Json
  foreach ($p in $req.pages) { $wanted[([string]$p.page_guid).ToUpperInvariant()] = $true }
}
$pageNodes = @($h.SelectNodes('//one:Notebook', $hm) | Where-Object { -not $Notebook -or $_.name -eq $Notebook } |
  ForEach-Object { $_.SelectNodes('.//one:Section', $hm) } |
  Where-Object { -not $Section -or $_.name -eq $Section } |
  ForEach-Object { $_.SelectNodes('one:Page', $hm) } |
  Where-Object { $_.isInRecycleBin -ne 'true' })
$chosen = New-Object System.Collections.Generic.List[object]
$found = @{}
foreach ($p in $pageNodes) {
  $link = Page-Link $p.ID; $guid = Page-Guid $link
  if (-not $guid) { continue }
  if ($Request -and -not $wanted.ContainsKey($guid)) { continue }
  if ($found.ContainsKey($guid)) { throw "Refusing: page GUID $guid appears twice in the hierarchy." }
  $found[$guid] = $true
  $chosen.Add([pscustomobject]@{ id = $p.ID; guid = $guid; link = $link })
}
$tsv = New-Object System.Collections.Generic.List[string]; $tsv.Add("tag`tpage_id`tpage_guid`tlink")
$n = 0; foreach ($c in $chosen) { $n++; $c | Add-Member tag ('{0:D3}' -f $n); $tsv.Add("$($c.tag)`t$($c.id)`t$($c.guid)`t$($c.link)") }
Utf8 (Join-Path $OutDir 'pages.tsv') (($tsv -join "`n") + "`n")
if ($Request) {
  $missing = @($wanted.Keys | Where-Object { -not $found.ContainsKey($_) } | Sort-Object)
  Utf8 (Join-Path $OutDir 'missing.tsv') ((@('page_guid') + $missing) -join "`n")
}

# 2. Each page: read; navigate only while placeholders or uncached files remain; copy.
$captured = 0; [long]$total = 0
foreach ($c in $chosen) {
  $dir = Join-Path $OutDir $c.tag; New-Item -ItemType Directory -Force (Join-Path $dir 'files') | Out-Null
  $pre = Read-Page $c.id; Utf8 (Join-Path $dir 'pre.xml') $pre
  [xml]$preDoc = $pre
  $empties = Empty-OEs $preDoc
  $t0 = [DateTime]::UtcNow.ToString('o'); $navigated = $false; $polls = 0
  $post = $pre; [xml]$pd = $post
  if (@($empties).Count -gt 0 -or (Uncached $preDoc) -gt 0) {
    $navigated = $true
    Invoke-Busy { $app.NavigateTo($c.id, '', $false) } | Out-Null; Start-Sleep -Seconds 3
    foreach ($e in $empties) { try { Invoke-Busy { $app.NavigateTo($c.id, $e.objectID, $false) } | Out-Null } catch { }; Start-Sleep -Milliseconds 1500 }
    while ($polls -lt $MaxPolls) {
      Start-Sleep -Seconds 3; $polls++
      $post = Read-Page $c.id; [xml]$pd = $post
      if ((Empty-OEs $pd).Count -eq 0 -and (Uncached $pd) -eq 0) { break }
    }
  } else {
    $post = Read-Page $c.id; [xml]$pd = $post
  }
  $t1 = [DateTime]::UtcNow.ToString('o')
  Utf8 (Join-Path $dir 'post.xml') $post
  $receipts = @(); $k = 0
  foreach ($f in (Files $pd)) {
    $k++; $oe = $f.ParentNode; $pc = $f.pathCache
    $floating = ($oe.LocalName -eq 'Page')
    $r = [ordered]@{ ordinal = $k; object_id = $(if ($floating) { $f.objectID } else { $oe.objectID }); preferred_name = $f.preferredName; floating = $floating
      oe_created = $(if ($floating) { '' } else { $oe.creationTime }); oe_modified = $(if ($floating) { $f.lastModifiedTime } else { $oe.lastModifiedTime }); status = '' }
    if (-not ($pc -and (Test-Path -LiteralPath $pc))) { $r.status = 'NOT_FETCHED'; $receipts += [pscustomobject]$r; continue }
    $len = (Get-Item -LiteralPath $pc).Length
    if ($captured -ge $MaxFiles) { $r.status = 'NOT_TESTED_FILE_BUDGET'; $receipts += [pscustomobject]$r; continue }
    if ($len -gt $MaxFile) { $r.status = 'NOT_TESTED_FILE_TOO_LARGE'; $receipts += [pscustomobject]$r; continue }
    if ($total + $len -gt $MaxTotal) { $r.status = 'NOT_TESTED_TOTAL_BUDGET'; $receipts += [pscustomobject]$r; continue }
    $ext = [System.IO.Path]::GetExtension([string]$f.preferredName).ToLowerInvariant()
    if (-not ($ext -match '^\.[a-z0-9]{1,6}$')) { $ext = '.bin' }
    $dest = Join-Path (Join-Path $dir 'files') ('{0:D3}{1}' -f $k, $ext)
    $h1 = (Get-FileHash -LiteralPath $pc -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $pc -Destination $dest
    $h2 = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
    $h3 = (Get-FileHash -LiteralPath $pc -Algorithm SHA256).Hash
    $r.sha256 = $h2.ToLowerInvariant(); $r.bytes = (Get-Item -LiteralPath $dest).Length
    $r.copy_matches_source = ($h1 -eq $h2); $r.source_stable = ($h1 -eq $h3); $r.copied_at = [DateTime]::UtcNow.ToString('o')
    $r.status = 'CAPTURED'; $captured++; $total += $r.bytes
    $receipts += [pscustomobject]$r
  }
  $meta = [pscustomobject]@{ tag = $c.tag; page_id = $c.id; page_guid = $c.guid; navigated = $navigated; pre_empty = @($empties).Count
    unresolved_after = (Empty-OEs $pd).Count; uncached_after = (Uncached $pd); post_files = (Files $pd).Count
    navigate_start = $t0; capture_end = $t1; polls = $polls
    title_pre_equals_post = ($preDoc.DocumentElement.name -eq $pd.DocumentElement.name); id_bound = ($pd.DocumentElement.ID -eq $c.id)
    receipts = $receipts }
  Utf8 (Join-Path $dir 'receipt.json') ($meta | ConvertTo-Json -Depth 6)
  "{0}`tnavigated={1}`tplaceholders_left={2}`tfiles={3}`tcaptured_total={4}" -f $c.tag, $navigated, $meta.unresolved_after, $meta.post_files, $captured
}

# 3. The hierarchy after (the verifier compares it), and the page the operator had open.
Utf8 (Join-Path $OutDir 'hierarchy-after.xml') (Read-Hierarchy)
if ($originalPage) {
  try { $app.NavigateTo($originalPage, '', $false); $log.Add([pscustomobject]@{ event = 'restored'; at = [DateTime]::UtcNow.ToString('o') }) }
  catch { $log.Add([pscustomobject]@{ event = 'restore_failed'; at = [DateTime]::UtcNow.ToString('o') }) }
}
$log.Add([pscustomobject]@{ event = 'end'; at = [DateTime]::UtcNow.ToString('o'); pages = $chosen.Count; files = $captured; bytes = $total; busy_retries = $script:busyRetries })
Utf8 (Join-Path $OutDir 'run-log.json') ($log | ConvertTo-Json -Depth 3)
"pages=$($chosen.Count) captured=$captured bytes=$total busy_retries=$script:busyRetries"
