@echo off
REM Jinx AI CLI - Terminal command wrapper

set JINX_HOME="%~dp0..\dist\JinxAI.exe"
set JINX_MODELS_DIR="%~dp0..\dist\JinxAI.exe\models"

"%JINX_HOME%\JinxAI.exe" %*
