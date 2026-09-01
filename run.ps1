# Local dashboard on http://127.0.0.1:8768
# Hot reload is on: edit anything under web/ or collector/ and the browser refreshes.
# Pass -NoReload to serve without it.
param([switch]$NoReload, [switch]$Debug, [int]$Port = 8768)
$args = @()
if ($NoReload) { $args += "--no-reload" }
if ($Debug)    { $args += "--debug" }
python web/app.py --port $Port @args
