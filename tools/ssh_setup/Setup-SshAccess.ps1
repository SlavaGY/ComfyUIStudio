#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Автоматическая настройка OpenSSH-сервера на Windows для доступа
    ComfyUI Studio с телефона по SSH-туннелю -- программный аналог
    шагов 1-6 и 8 из SSH_Proxy_Server_Setup.md.

.DESCRIPTION
    Шаг 7 гайда (проброс порта на роутере) сюда сознательно НЕ входит --
    это настройка роутера, у каждой модели свой интерфейс, скрипт не
    может сделать это за пользователя. Если ПК доступен снаружи через
    IPv6 (см. ComfyUIStudio_Remote_Roadmap.md про диагностику CGNAT) --
    шаг 7 просто не нужен, см. ту же переписку.

    Каждый шаг ниже воспроизводит РОВНО то, что в соответствующем шаге
    гайда делалось руками, той же командой -- расхождения с гайдом
    отмечены явно в комментариях у самого шага.

    ВАЖНО (эксплуатационная честность): этот скрипт ещё НЕ проверен
    живым тестом на реальной машине (в отличие от самого гайда -- см.
    его последнюю строку про то, что шаг 6 подтверждён 2026-09-21).
    Перед тем как полагаться на кнопку в Studio, стоит один раз прогнать
    его вручную и глазами свериться с гайдом на случай, если что-то в
    конкретной сборке Windows пойдёт иначе, чем предполагалось (особенно
    шаг 2 -- Start-Process -Credential для создания профиля -- это
    неинтерактивный аналог `runas /user:... cmd` из гайда, который сам
    по себе живым тестом не проверялся).

.PARAMETER Username
    Имя урезанной учётной записи только для проброса портов (шаг 2
    гайда). По умолчанию -- то же имя, что и в гайде.

.PARAMETER SshPort
    Порт, на котором будет слушать sshd (шаг 7 гайда, часть про смену
    порта с 22 -- сама смена делается здесь, проброс на роутере -- нет,
    см. .DESCRIPTION).

.PARAMETER PermitOpenHost
    Адрес, к которому sshd разрешит проброс (см. `PermitOpen` в шаге 5
    гайда) -- то, что видит сам Windows-сервер локально. Обычно это
    127.0.0.1: sshd и Remote всегда на одной машине, так что достаточно
    localhost независимо от того, по какому адресу к этой машине
    подключается сам телефон (в отличие от примера в гайде, где взят
    LAN IP -- то же самое верно и проще, LAN-адрес не может внезапно
    смениться по DHCP и сломать правило).

.PARAMETER PermitOpenPort
    Порт Remote на этой же машине (см. PermitOpenHost выше).

.PARAMETER OutputJsonPath
    Куда записать результат (приватный/публичный ключ, кандидаты
    IPv6-адреса, предупреждения) -- читается родительским процессом
    (comfyui_studio/launcher/core/ssh_setup.py), который затем сам
    удаляет этот файл (см. его докстринг про то, почему он не должен
    жить на диске дольше необходимого).
#>
param(
    [string]$Username = "comfyui-ssh",
    [int]$SshPort = 2222,
    [string]$PermitOpenHost = "127.0.0.1",
    [int]$PermitOpenPort = 7861,
    [Parameter(Mandatory = $true)]
    [string]$OutputJsonPath
)

$ErrorActionPreference = "Stop"
$warnings = New-Object System.Collections.Generic.List[string]

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Write-Result {
    param([hashtable]$Result)
    # ЖИВОЙ ТЕСТ 2026-09-27: "Не удалось найти часть пути" -- Out-File
    # не создаёт родительские папки сам, а $OutputJsonPath пришёл с
    # несуществующей директорией (ручной запуск с -OutputJsonPath
    # "C:\temp\..." при отсутствующей C:\temp; через кнопку в Studio
    # такого не бывает -- там путь всегда в $env:TEMP, которая
    # гарантированно существует, -- но лучше не зависеть от этого).
    $outDir = Split-Path -Path $OutputJsonPath -Parent
    if ($outDir -and -not (Test-Path -Path $outDir)) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }
    $Result | ConvertTo-Json -Depth 4 | Out-File -FilePath $OutputJsonPath -Encoding utf8
}

try {
    # -- Шаг 1: OpenSSH Server ------------------------------------------
    Write-Step "Шаг 1/7: OpenSSH Server"
    $serverCap = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    if ($serverCap.State -ne "Installed") {
        Write-Host "Устанавливаю OpenSSH.Server..."
        Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
    } else {
        Write-Host "Уже установлен."
    }
    Set-Service -Name sshd -StartupType Automatic
    Start-Service sshd -ErrorAction SilentlyContinue

    # ssh-keygen нужен ниже (шаг 3) -- идёт вместе с OpenSSH.Client, на
    # большинстве Windows 10/11 уже стоит по умолчанию, но не гарантировано.
    if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
        $clientCap = Get-WindowsCapability -Online -Name "OpenSSH.Client~~~~0.0.1.0"
        if ($clientCap.State -ne "Installed") {
            Write-Host "Устанавливаю OpenSSH.Client (нужен ssh-keygen)..."
            Add-WindowsCapability -Online -Name "OpenSSH.Client~~~~0.0.1.0" | Out-Null
        }
    }

    # -- Шаг 2: учётная запись ------------------------------------------
    Write-Step "Шаг 2/7: учётная запись $Username"
    # Случайный пароль без зависимости от System.Web -- нужен только
    # один раз ниже, чтобы Windows создала профиль учётки (см. шаг 2
    # гайда, абзац про "runas /user:... cmd"); по SSH он всё равно не
    # принимается (см. шаг 6 гайда) и нигде не сохраняется.
    $randomPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    $securePassword = ConvertTo-SecureString $randomPassword -AsPlainText -Force

    $existingUser = Get-LocalUser -Name $Username -ErrorAction SilentlyContinue
    if (-not $existingUser) {
        Write-Host "Создаю новую учётную запись..."
        New-LocalUser -Name $Username -Password $securePassword `
            -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    } else {
        Write-Host "Учётная запись уже существует -- обновляю пароль для разового входа ниже."
        Set-LocalUser -Name $Username -Password $securePassword
    }
    $credential = New-Object System.Management.Automation.PSCredential($Username, $securePassword)
    $userSid = (Get-LocalUser -Name $Username).SID.Value

    function Get-ProfilePath {
        param([string]$Sid)
        $prof = Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $Sid }
        if ($prof) { return $prof.LocalPath }
        return $null
    }

    # Реальный путь профиля берём через Win32_UserProfile, а не
    # C:\Users\$Username напрямую -- Windows может создать профиль с
    # суффиксом ...\$Username.<ИМЯ-ПК>, если папка с ожидаемым именем
    # уже была занята (см. гайд, шаг 2, про "пустышку").
    $profilePath = Get-ProfilePath -Sid $userSid
    if (-not $profilePath) {
        Write-Host "Профиль ещё не создан -- выполняю разовый вход, чтобы Windows его создала..."
        # Неинтерактивный аналог `runas /user:$Username cmd` из гайда --
        # у нас уже есть пароль учётки (сгенерирован выше), поэтому вход
        # можно выполнить без диалога. -Wait гарантирует, что профиль уже
        # существует к моменту следующей проверки ниже.
        Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList "/c", "exit" `
            -Credential $credential -WorkingDirectory "$env:SystemRoot\System32" -Wait
        Start-Sleep -Seconds 2
        $profilePath = Get-ProfilePath -Sid $userSid
    }
    if (-not $profilePath) {
        throw "Не удалось определить путь профиля пользователя ${Username}: разовый вход не создал профиль (см. .DESCRIPTION про этот шаг)."
    }
    Write-Host "Профиль: $profilePath"

    # -- Шаг 3: пара ключей для телефона --------------------------------
    Write-Step "Шаг 3/7: пара ключей"
    $keyDir = Join-Path $env:TEMP "comfyui_studio_ssh_keygen_$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $keyDir | Out-Null
    $keyPath = Join-Path $keyDir "phone_key"
    & ssh-keygen -t ed25519 -f $keyPath -N '""' -C "comfyui-studio-phone" | Out-Null
    if (-not (Test-Path "$keyPath") -or -not (Test-Path "$keyPath.pub")) {
        throw "ssh-keygen не создал пару ключей в $keyDir."
    }
    # ЖИВОЙ ТЕСТ 2026-09-27: на стороне Studio пришла ошибка валидации
    # "private_key: input should be a valid string", а в качестве
    # "input" -- дамп PowerShell-объекта FileSystemProvider (PSChildName,
    # PSDrive, ProviderCmdlet и т.п.), а не текст ключа. Механизм, из-за
    # которого Get-Content -Raw в этом месте вернул не строку, а объект,
    # не воспроизведён живьём -- явную проверку типа/формата ставим
    # здесь, чтобы либо поймать порчу ДО отправки на Remote с понятным
    # сообщением в консоли, либо, если проблема не в этом, сузить круг
    # поиска для следующего живого теста.
    $privateKeyText = [string](Get-Content -Path $keyPath -Raw)
    $publicKeyText = [string](Get-Content -Path "$keyPath.pub" -Raw).Trim()
    if ($privateKeyText -notmatch "-----BEGIN OPENSSH PRIVATE KEY-----") {
        throw "Get-Content вернул не похожее на приватный ключ содержимое ($keyPath) -- см. .DESCRIPTION про этот шаг. Начало полученного значения: $($privateKeyText.Substring(0, [Math]::Min(120, $privateKeyText.Length)))"
    }

    # -- Шаг 4: authorized_keys + права ----------------------------------
    Write-Step "Шаг 4/7: authorized_keys"
    $sshDir = Join-Path $profilePath ".ssh"
    $authKeysPath = Join-Path $sshDir "authorized_keys"
    New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
    Set-Content -Path $authKeysPath -Value $publicKeyText -Encoding ascii -NoNewline

    # ЖИВОЙ ТЕСТ 2026-09-27 нашёл баг в порядке команд: изначально здесь
    # сначала стояло /inheritance:r, ПОТОМ /grant -- `/inheritance:r` (в
    # отличие от /inheritance:d) не копирует унаследованные права, а
    # СНОСИТ их, так что папка на мгновение остаётся вообще без явных
    # прав ни у кого, включая текущий (админский) процесс; последующие
    # /grant ... /T падали с "Отказано в доступе" на "$sshDir\*", потому
    # что /T пытается ПЕРЕЧИСЛИТЬ содержимое папки, чтобы применить права
    # и к authorized_keys внутри, а перечисление (в отличие от смены
    # самого DACL, которая владельцу доступна всегда через WRITE_DAC)
    # требует прав на чтение папки, которых уже не было. Правильный
    # порядок -- сначала выдать права (пока ещё действует наследование от
    # профиля, т.е. точно есть чем перечислить содержимое), и только
    # ПОТОМ убрать наследование и сменить владельца -- права уже на
    # месте, терять нечего.
    icacls $sshDir /grant "${Username}:(OI)(CI)F" /T | Out-Null
    icacls $sshDir /grant "SYSTEM:(OI)(CI)F" /T | Out-Null
    icacls $sshDir /grant "Администраторы:(OI)(CI)F" /T | Out-Null
    icacls $sshDir /inheritance:r | Out-Null
    icacls $sshDir /setowner "$Username" /T | Out-Null

    # Очищаем временную копию ключей с диска -- дальше приватный ключ
    # существует только в памяти этого процесса (переменная выше) и в
    # OutputJsonPath, который родительский процесс читает и сразу удаляет.
    Remove-Item -Path $keyDir -Recurse -Force -ErrorAction SilentlyContinue

    # -- Шаг 5: ограничение учётной записи + PermitOpen ------------------
    Write-Step "Шаг 5/7: ограничение Match-блока"
    $sshdConfigPath = "$env:ProgramData\ssh\sshd_config"
    $configText = Get-Content -Path $sshdConfigPath -Raw

    if ($configText -match "(?m)^\s*Match\s+User\s+$([regex]::Escape($Username))\b") {
        $warnings.Add("Match-блок для пользователя $Username уже есть в sshd_config -- не трогаю его; проверьте вручную, что PermitOpen там указывает на ${PermitOpenHost}:${PermitOpenPort}.")
    } else {
        $matchBlock = @"

Match User $Username
    AllowTcpForwarding yes
    PermitOpen ${PermitOpenHost}:${PermitOpenPort}
    X11Forwarding no
    PermitTTY no
    ForceCommand echo "Эта учётная запись -- только для проброса портов"
"@
        Add-Content -Path $sshdConfigPath -Value $matchBlock
    }

    # -- Шаг 6: вход только по ключу (общая часть, ВЫШЕ первого Match) --
    Write-Step "Шаг 6/7: отключение входа по паролю"
    $lines = Get-Content -Path $sshdConfigPath
    $firstMatchIndex = ($lines | Select-String -Pattern "^\s*Match\s" | Select-Object -First 1).LineNumber
    if (-not $firstMatchIndex) { $firstMatchIndex = $lines.Count + 1 }
    $generalLines = $lines[0..($firstMatchIndex - 2)]
    $restLines = $lines[($firstMatchIndex - 1)..($lines.Count - 1)]

    # ЖИВОЙ ТЕСТ 2026-09-27: "Port $SshPort" сюда же, а не отдельным
    # Add-Content ниже -- Add-Content дописывает в КОНЕЦ ФАЙЛА, а к этому
    # моменту в конце файла уже лежит Match-блок из шага 5, так что порт
    # утекал ПОСЛЕ Match и попадал в его контекст: "Directive 'Port' is
    # not allowed within a Match block". [ordered], а не просто @{} --
    # чтобы Port гарантированно оказался выше остальных строк общей
    # секции, а не в случайном порядке хеш-таблицы.
    $desired = [ordered]@{
        "Port"                        = "$SshPort"
        "PasswordAuthentication"      = "no"
        "KbdInteractiveAuthentication" = "no"
        "PubkeyAuthentication"        = "yes"
    }
    # Убираем из общей части любые НЕзакомментированные строки с этими
    # ключами (старое значение, если оно там уже было -- в стандартном
    # файле это обычно закомментированная "#PasswordAuthentication yes",
    # её оставляем как есть, мы ориентируемся на активные строки).
    $filteredGeneral = $generalLines | Where-Object {
        $line = $_
        -not ($desired.Keys | Where-Object { $line -match "^\s*$_\s" })
    }
    $newDirectives = $desired.GetEnumerator() | ForEach-Object { "$($_.Key) $($_.Value)" }
    $newConfig = $filteredGeneral + $newDirectives + $restLines
    Set-Content -Path $sshdConfigPath -Value $newConfig -Encoding ascii

    & "$env:SystemRoot\System32\OpenSSH\sshd.exe" -t
    if ($LASTEXITCODE -ne 0) {
        throw "sshd -t сообщил об ошибке в конфиге -- проверьте $sshdConfigPath вручную."
    }
    Restart-Service sshd

    # -- Шаг 7 (по нумерации гайда -- 8): firewall -----------------------
    Write-Step "Шаг 7/7: правило файрвола"
    $ruleName = "ComfyUI Studio SSH ($SshPort)"
    $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
            -LocalPort $SshPort -Action Allow -Profile Domain, Private, Public | Out-Null
    } else {
        Write-Host "Правило файрвола уже существует."
    }

    # -- Кандидаты IPv6-адреса для поля "Адрес сервера" на телефоне ------
    $ipv6Candidates = @(
        Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notmatch "^fe80" -and $_.IPAddress -notmatch "^::1$"
            } |
            Select-Object -ExpandProperty IPAddress
    )

    Write-Result -Result @{
        error             = $null
        username          = $Username
        ssh_port          = $SshPort
        permit_open_host  = $PermitOpenHost
        permit_open_port  = $PermitOpenPort
        private_key       = $privateKeyText
        public_key        = $publicKeyText
        ipv6_candidates   = $ipv6Candidates
        warnings          = @($warnings)
    }
    Write-Host ""
    Write-Host "Готово." -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
    try {
        Write-Result -Result @{
            error    = $_.Exception.Message
            warnings = @($warnings)
        }
    } catch {
        # Даже записать ошибку не вышло (например OutputJsonPath
        # недоступен для записи) -- родительский процесс поймёт это по
        # отсутствию файла и своему собственному сообщению об ошибке.
    }
}
finally {
    Read-Host "Нажмите Enter, чтобы закрыть это окно"
}
