# POL ADV Notifier — Desktop (.NET 10 + WPF)

Reescrita da edição desktop (`app/`, Python + CustomTkinter) em .NET 10 WPF. A edição web (`app-web.py`) continua em Python.

## Compilar e rodar

```powershell
.\desktop-wpf\build.ps1            # Debug (ou -Configuration Release)
dotnet run --project desktop-wpf\PolNotifier
```

O `nuget.config` desta pasta aponta para o nuget.org (o NuGet global da máquina está sem fontes).

## Publicar e gerar o instalador

```powershell
.\desktop-wpf\publish.ps1 -Version 2.1.3   # sem -Version usa a última tag vX.Y.Z
```

O script gera `publish\PolNotifier.exe` (self-contained single-file, não precisa do .NET instalado) e, se o NSIS estiver instalado (`winget install NSIS.NSIS`), também `publish\PolNotifier-Setup-<versão>.exe`.

O instalador (`installer\PolNotifier.nsi`) é por usuário e não pede administrador. Ele instala em `%LOCALAPPDATA%\Programs\POL ADV Notifier` e oferece três opções: atalho na Área de Trabalho, pasta no Menu Iniciar e iniciar junto com o Windows. Ao atualizar, ele fecha a versão aberta. Desinstalar mantém os dados e a senha lembrada.

O início automático também pode ser ligado e desligado em **Configurações → Inicialização**. Os dois usam o mesmo valor `POL ADV Notifier` em `HKCU\...\CurrentVersion\Run`.

## CI/CD (GitHub Actions)

- **Desktop:** `.github/workflows/desktop.yml` compila em cada push ou PR que mexa em `desktop-wpf/`. Numa tag `vX.Y.Z`, publica o `.exe` e o instalador como release marcada como latest. O link fixo para a versão mais nova é `https://github.com/sergiobaiao/pol_monitor/releases/latest/download/PolNotifier-Setup.exe`.
- **Web:** `.github/workflows/web.yml` faz o deploy do `app-web.py` no Worker (`npx wrangler deploy`) a cada push em `main` que mexa nele e em cada tag. Ele precisa dos segredos `CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID` no repositório.

Para lançar uma versão, use o `release.ps1` da raiz (cria e envia a próxima tag):

```powershell
.elease.ps1                  # patch (2.1.2 -> 2.1.3); -Bump minor|major; -DryRun só mostra
```

## Dados (compatíveis com a versão Python)

- **Pasta `data/`:** o app sobe a partir do .exe até achar `data\pol_notifier.db` (rodando de `desktop-wpf\bin\...` ou `desktop-wpf\publish\` ele acha `Z:\pol\data`). Instalado pelo NSIS, usa `%LOCALAPPDATA%\POL ADV Notifier\data`. A variável `POL_DATA_DIR` tem precedência. Para levar as configurações atuais para a versão instalada, copie `Z:\pol\data` para essa pasta ou defina `POL_DATA_DIR=Z:\pol\data`.
- **Bancos:** `data\users\<sub>.db` guarda as configurações, o cache e o histórico de cada usuário, no mesmo esquema de `app/database.py`. `data\pol_notifier.db` guarda só o e-mail lembrado.
- **Senha lembrada:** fica no Windows Credential Manager, no mesmo formato do `keyring` do Python. É uma credencial genérica com alvo `POL ADV Notifier` e usuário = e-mail (ou alvo `e-mail@POL ADV Notifier` quando outro e-mail já ocupa o alvo simples). Uma senha lembrada por uma versão vale na outra.
- **Instância única:** abrir o .exe de novo mostra o painel da instância que já está aberta. Não rode a versão Python ao mesmo tempo: o agendador e as notificações ficariam duplicados.

## Ícone

O ícone (`PolNotifier/app.ico`) e a imagem das telas (`PolNotifier/logo.png`) vêm de `assets/app-icon-original.png`. O `app.ico` define o ícone do .exe, das janelas, da bandeja e do instalador.

## Painel

Abas Início, Tarefas (Lista e Kanban), Calendário (Mês e Lista, com popup de detalhes ao clicar), Notificações, Configurações e Logs. O botão **📨 Relatório** da barra superior (e o item da bandeja) reenvia o último relatório aos canais ativos.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `Core/Database.cs`, `Core/Config.cs` | SQLite e configurações (chaves e padrões de `app/config.py`) |
| `Core/CredentialStore.cs` | Credential Manager no formato do keyring |
| `Core/PolClient.cs` | Login no Keycloak, refresh token e busca de tarefas e eventos (com paginação) |
| `Core/Formatter.cs`, `Core/Notifiers.cs`, `Core/Scheduler.cs` | Classificação, e-mail (MailKit), Telegram, OpenWA e ciclo com deduplicação diária |
| `Ui/FenceWindow.*` | Widget de desktop |
| `Ui/MainWindow.*`, `Ui/LoginWindow.*`, `Ui/Alert.cs` | Painel, login e modal |
| `App.xaml.cs` | Mutex, login, sessão do usuário e bandeja (`NotifyIcon`) |

## Widget de desktop

O widget é uma janela top-level com `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`. O **owner** dela é o `Progman` (`SetWindowLongPtr(GWLP_HWNDPARENT)`) e ela fica em `HWND_BOTTOM`. Um timer de 1 s refaz o owner se o Explorer reiniciar.

- **Não usar `SetParent`:** no Windows 11 24H2+ o Progman tem `WS_EX_NOREDIRECTIONBITMAP`, e uma janela filha de outro processo não é desenhada.
- **Não restaurar o widget por conta própria:** se o app restaurar o widget quando ele for minimizado ou ocultado, o segundo Win + D deixa de devolver as janelas.

Posição em pixels físicos e tamanho em pixels lógicos, como o Tk gravava.

## Diferenças em relação ao Python

- **Datas:** as datas da API vêm em UTC (`...T02:59:59Z`) e são convertidas para o horário local. O Python classificava e exibia pela data UTC, então um prazo das 23:59 aparecia como 02:59 do dia seguinte. Prazos de "fim do dia" (23:59 local) aparecem só com a data.
- **Paginação:** as tarefas são buscadas com `?active=true` e paginadas (o Python parava nas 100 primeiras).
- **Novidades:** o widget pode ser recolhido (setting `desktop_fence_collapsed`), e há a opção "SSL direto (porta 465)" no e-mail.
