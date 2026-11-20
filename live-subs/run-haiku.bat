@echo off
rem Start Live Subs with the cheaper Claude Haiku 5.5 translator
call "%~dp0run.bat" --translator claude --claude-model claude-haiku-5-5 %*
