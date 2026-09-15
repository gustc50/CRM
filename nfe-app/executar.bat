@echo off
REM Abre a instalacao/execucao numa janela de console separada que fica
REM aberta mesmo se algo der errado logo no inicio (nao fecha sozinha).
title Consulta de NFe
start "Consulta de NFe" cmd /k call "%~dp0_executar_core.bat"
