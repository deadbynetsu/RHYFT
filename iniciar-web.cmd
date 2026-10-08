@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 goto directory_error

where node >nul 2>&1
if errorlevel 1 goto node_missing
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1);"
if errorlevel 1 goto node_old

if not exist "web-server.cjs" goto server_missing

rem O segredo permanece neste computador e nunca e exibido no terminal.
set "SESSION_SECRET="
node -e "const fs = require('node:fs'); const crypto = require('node:crypto'); const file = '.rhyft-web-session-secret'; try { let secret; try { secret = fs.readFileSync(file, 'utf8').trim(); } catch (error) { if (error.code !== 'ENOENT') throw error; const generated = crypto.randomBytes(32).toString('hex'); try { fs.writeFileSync(file, generated + '\n', {flag: 'wx', mode: 0o600}); secret = generated; } catch (writeError) { if (writeError.code !== 'EEXIST') throw writeError; secret = fs.readFileSync(file, 'utf8').trim(); } } if (!/^[a-f0-9]{64}$/.test(secret)) throw new Error('invalid session secret'); } catch { console.error('Nao foi possivel preparar .rhyft-web-session-secret. Verifique o arquivo e a permissao desta pasta.'); process.exitCode = 1; }"
if errorlevel 1 goto secret_error
set /p "SESSION_SECRET=" < ".rhyft-web-session-secret"
if not defined SESSION_SECRET goto secret_error

set "HOST=127.0.0.1"
set "PORT=8787"
set "SITE_URL=http://127.0.0.1:8787"
set "NETLIFY_DEV=true"
set "TRUST_PROXY=0"

echo Iniciando RHYFT Web em %SITE_URL%/migrar.html
echo Mantenha esta janela aberta. Para encerrar, pressione Ctrl+C.
echo As credenciais sao lidas de .env.web; variaveis existentes do Windows tem prioridade.
echo.

rem Abre o navegador apenas quando o servidor responde, por ate 30 segundos.
start "" /b node -e "const http = require('node:http'); const {spawn} = require('node:child_process'); const url = process.env.SITE_URL; const deadline = Date.now() + 30000; let finished = false; const retry = () => { if (!finished && Date.now() < deadline) setTimeout(probe, 500); }; const probe = () => { const request = http.get(url + '/health', response => { response.resume(); if (response.statusCode !== 200) return retry(); if (finished) return; finished = true; const browser = spawn('cmd.exe', ['/d', '/c', 'start', '', url + '/migrar.html'], {windowsHide: true, detached: true, stdio: 'ignore'}); browser.on('error', () => {}); browser.unref(); }); request.setTimeout(1000, () => request.destroy()); request.on('error', retry); }; probe();"
if exist ".env.web" (
    node --env-file=.env.web web-server.cjs
) else (
    node web-server.cjs
)
set "RHYFT_EXIT_CODE=%ERRORLEVEL%"
echo.
echo O servidor RHYFT Web foi encerrado.
pause
exit /b %RHYFT_EXIT_CODE%

:node_missing
echo Node.js nao foi encontrado. Instale a versao LTS atual em https://nodejs.org/
echo Depois da instalacao, abra este arquivo novamente.
goto setup_error

:node_old
echo Este iniciador precisa do Node.js 22 ou mais recente.
echo Atualize o Node.js em https://nodejs.org/ e abra este arquivo novamente.
goto setup_error

:server_missing
echo web-server.cjs nao foi encontrado. Extraia todos os arquivos do RHYFT na mesma pasta.
goto setup_error

:directory_error
echo Nao foi possivel abrir a pasta deste iniciador.
goto setup_error

:secret_error
echo Nao foi possivel iniciar uma sessao local segura.
goto setup_error

:setup_error
pause
exit /b 1
