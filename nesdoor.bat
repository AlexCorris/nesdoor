@echo off
rem Windows launcher when running from Python instead of nesdoor.exe
rem Synchronet command line example:  C:\sbbs\xtrn\nesdoor\nesdoor.bat %n
cd /d "%~dp0"
python nesdoor.py roms %* --hold 100 2>>nesdoor.log
