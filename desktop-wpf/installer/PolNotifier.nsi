; Instalador do POL ADV Notifier (por usuário, sem pedir administrador).
; Gerado por publish.ps1 / CI: makensis /DVERSION=2.1.0 /DNUMVERSION=2.1.0.0 /DSRCDIR=..\publish /DOUTFILE=Setup.exe PolNotifier.nsi
Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
  !define NUMVERSION "0.0.0.0"
!endif
!define APPNAME "POL ADV Notifier"
!define EXE "PolNotifier.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolAdvNotifier"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${NUMVERSION}"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "Instalador do ${APPNAME}"
VIAddVersionKey "LegalCopyright" "Sérgio Baião"

!define MUI_ICON "..\PolNotifier\app.ico"
!define MUI_UNICON "..\PolNotifier\app.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Abrir o ${APPNAME}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

; Fecha a versão em execução para poder substituir o .exe (atualização)
!macro CloseApp
  nsExec::Exec 'taskkill /IM ${EXE} /F'
  Sleep 800
!macroend

Section "!${APPNAME}" SecApp
  SectionIn RO
  !insertmacro CloseApp
  SetOutPath "$INSTDIR"
  File "${SRCDIR}\${EXE}"
  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Sérgio Baião"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Atalho na Área de Trabalho" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE}"
SectionEnd

Section "Pasta no Menu Iniciar" SecStartMenu
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Desinstalar ${APPNAME}.lnk" "$INSTDIR\Desinstalar.exe"
SectionEnd

; Mesmo valor que a opção "Iniciar com o Windows" das Configurações do app grava
Section "Iniciar junto com o Windows" SecStartup
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APPNAME}" '"$INSTDIR\${EXE}"'
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "Arquivos do aplicativo (obrigatório)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Cria um ícone do ${APPNAME} na Área de Trabalho."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} "Cria a pasta ${APPNAME} no Menu Iniciar."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartup} "Abre o ${APPNAME} automaticamente ao entrar no Windows (pode ser alterado depois nas Configurações do app)."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  !insertmacro CloseApp
  ; Os dados (data\, Credential Manager) ficam: reinstalar mantém configurações e senha lembrada
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\Desinstalar.exe"
  RMDir "$INSTDIR"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  Delete "$DESKTOP\${APPNAME}.lnk"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APPNAME}"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
