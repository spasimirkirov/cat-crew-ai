@echo off
REM CatCrewAI CLI - Terminal command wrapper

set CATCREW_HOME="%~dp0..\dist\CatCrewAI.exe"
set CATCREW_MODELS_DIR="%~dp0..\dist\CatCrewAI.exe\models"

"%CATCREW_HOME%\CatCrewAI.exe" %*
