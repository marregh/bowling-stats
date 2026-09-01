# Run during a match at the home alley. Ctrl+C to stop.
# Poll every 75s so every game's frames land before the sheet is overwritten.
param([string]$Slug = "lunds-bowling", [int]$Minutes = 0)
python collector/capture.py --slug $Slug --interval 75 --minutes $Minutes
