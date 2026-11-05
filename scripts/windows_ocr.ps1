param([string]$ImagePath, [string]$Language = 'en-US', [switch]$Server)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-Operation($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$engines = @{}
function Read-Image([string]$Path, [string]$Locale) {
$file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'Image exceeds Windows OCR dimensions' }
        if (!$engines.ContainsKey($Locale)) {
            $engines[$Locale] = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Locale))
        }
        $engine = $engines[$Locale]
        if ($null -eq $engine) { throw "OCR language unavailable: $Locale" }
        $result = Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @(); $number = 0
        foreach ($line in $result.Lines) {
            $number++; $words = @()
            foreach ($word in $line.Words) {
                $rect = $word.BoundingRect
                $words += @{text=$word.Text; box=@($rect.X,$rect.Y,($rect.X+$rect.Width),($rect.Y+$rect.Height))}
            }
            $lines += @{id="ocr$number"; text=$line.Text; words=$words}
        }
        return @{engine='windows.media.ocr'; language=$engine.RecognizerLanguage.LanguageTag; angle=$result.TextAngle; lines=$lines; width=$bitmap.PixelWidth; height=$bitmap.PixelHeight}
    } finally { if ($null -ne $bitmap) { $bitmap.Dispose() } }
} finally { $stream.Dispose() }
}
if ($Server) {
    while ($null -ne ($line = [Console]::ReadLine())) {
        try {
            $request = $line | ConvertFrom-Json
            if ($request.ping) { $reply = @{ready=$true} }
            else { $reply = Read-Image $request.path $request.language }
        } catch { $reply = @{error=$_.Exception.Message} }
        [Console]::WriteLine(($reply | ConvertTo-Json -Depth 8 -Compress))
        [Console]::Out.Flush()
    }
} else {
    if (!$ImagePath) { throw 'ImagePath is required' }
    Read-Image $ImagePath $Language | ConvertTo-Json -Depth 8 -Compress
}
