@echo off
echo ========================================
echo   低延迟直播观看器
echo ========================================
echo.
set /p url=Paste FLV URL:
echo.
echo Starting...
"%~dp0ffplay.exe" -fflags nobuffer -flags low_delay -framedrop -infbuf -max_delay 0 -sync ext -vf "setpts=0.9091*PTS" -af "atempo=1.1000" -window_title "极速播放" "%url%"
pause
