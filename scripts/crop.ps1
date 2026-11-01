# Crop a region from an image and save it upscaled: PNG by default, JPEG (quality 90)
# when -Out ends in .jpg/.jpeg, which keeps zoomed review crops small (lessons R007).
# Usage: crop.ps1 -Src in.webp -Out out.png -X 0 -Y 0 -W 100 -H 100 [-Scale 3]
param(
    [string]$Src, [string]$Out,
    [int]$X, [int]$Y, [int]$W, [int]$H,
    [double]$Scale = 3
)
Add-Type -AssemblyName PresentationCore
$stream = [IO.File]::OpenRead($Src)
try {
    $frame = [Windows.Media.Imaging.BitmapDecoder]::Create($stream, 'None', 'OnLoad').Frames[0]
    $rect = New-Object Windows.Int32Rect $X, $Y, $W, $H
    $crop = New-Object Windows.Media.Imaging.CroppedBitmap $frame, $rect
    $scaled = New-Object Windows.Media.Imaging.TransformedBitmap $crop, (New-Object Windows.Media.ScaleTransform $Scale, $Scale)
    if ($Out -match '\.jpe?g$') {
        $scaled = New-Object Windows.Media.Imaging.FormatConvertedBitmap $scaled, ([Windows.Media.PixelFormats]::Bgr24), $null, 0
        $enc = New-Object Windows.Media.Imaging.JpegBitmapEncoder
        $enc.QualityLevel = 90
    } else {
        $enc = New-Object Windows.Media.Imaging.PngBitmapEncoder
    }
    $enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($scaled))
    $fs = [IO.File]::Create($Out)
    try { $enc.Save($fs) } finally { $fs.Close() }
} finally { $stream.Close() }
Write-Output "saved $Out"
