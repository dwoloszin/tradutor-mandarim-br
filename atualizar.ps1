# Rodada local — a metade do trabalho que a nuvem não consegue fazer.
#
# A coleta da madrugada no GitHub Actions marca como "adiado_local" tudo que foi
# bloqueado por vir de IP de datacenter (Alibaba, TradeChina, sites com Cloudflare).
# Este script pega exatamente essa lista e completa usando a sua conexão residencial.
#
# Uso:
#   .\atualizar.ps1                # completa os pendentes e publica
#   .\atualizar.ps1 -Tudo          # ciclo completo, não só os pendentes
#   .\atualizar.ps1 -Limite 30     # limita quantas tarefas processar
#   .\atualizar.ps1 -SemPush       # roda e commita, mas não envia para o GitHub
#
# Para agendar todo dia às 19h (uma vez só, no PowerShell como administrador):
#   $acao    = New-ScheduledTaskAction -Execute "powershell.exe" `
#              -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\atualizar.ps1`""
#   $gatilho = New-ScheduledTaskTrigger -Daily -At 19:00
#   Register-ScheduledTask -TaskName "MandarimJob - coleta local" -Action $acao -Trigger $gatilho

param(
    [switch]$Tudo,
    [switch]$SemPush,
    [int]$Limite = 0
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:MJ_AMBIENTE = "local"
$env:PYTHONIOENCODING = "utf-8"

function Escrever($texto, $cor = "Cyan") {
    Write-Host ""
    Write-Host "== $texto" -ForegroundColor $cor
}

# --- 1. pegar o que a nuvem já coletou -------------------------------------
if (Test-Path ".git") {
    Escrever "Sincronizando com o GitHub"
    try {
        git pull --rebase --autostash
    } catch {
        Write-Host "   aviso: nao consegui dar pull ($_). Seguindo com os dados locais." -ForegroundColor Yellow
    }
}

# --- 2. rodar a coleta ------------------------------------------------------
$comando = if ($Tudo) { "tudo" } else { "pendentes" }
$argumentos = @("-m", "scraper.cli", $comando)
if ($Limite -gt 0) { $argumentos += @("--limite", "$Limite") }

Escrever "Rodando: python $($argumentos -join ' ')"
python @argumentos
if ($LASTEXITCODE -ne 0) {
    Write-Host "A coleta terminou com erro (codigo $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- 3. publicar ------------------------------------------------------------
if (-not (Test-Path ".git")) {
    Escrever "Sem repositorio Git: dados atualizados apenas nesta maquina." "Yellow"
    exit 0
}

Escrever "Publicando os dados novos"
git add data/*.jsonl docs/data/*.json

$pendente = git diff --cached --name-only
if (-not $pendente) {
    Write-Host "   nada mudou nesta rodada." -ForegroundColor DarkGray
    exit 0
}

$data = Get-Date -Format "yyyy-MM-dd HH:mm"
git commit -m "dados: rodada local $data"

if ($SemPush) {
    Write-Host "   commit feito; push pulado (-SemPush)." -ForegroundColor DarkGray
} else {
    try {
        git push
        Write-Host "   enviado para o GitHub." -ForegroundColor Green
    } catch {
        Write-Host "   nao consegui enviar ($_). O commit esta salvo localmente." -ForegroundColor Yellow
    }
}
