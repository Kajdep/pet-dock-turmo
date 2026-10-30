; Pet Dock per-user Windows installer
; SPDX-License-Identifier: GPL-3.0-or-later
; This installer intentionally has no drivers, auto-start entries, or machine-wide state.

Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef VERSION
  !error "VERSION must be supplied (for example -DVERSION=0.3.0)"
!endif
!ifndef STAGE
  !error "STAGE must be supplied with the portable payload directory"
!endif
!ifndef OUTPUT
  !error "OUTPUT must be supplied with the installer output directory"
!endif

Name "Pet Dock ${VERSION}"
OutFile "${OUTPUT}/Pet-Dock-Setup-v${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\PetDock"
BrandingText "Pet Dock ${VERSION}"
ShowInstDetails show
ShowUnInstDetails show

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${STAGE}/LICENSE"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; The install directory is deliberately fixed to the per-user location. This
; lets uninstallation remove only known Pet Dock paths without risking a folder
; chosen for unrelated files.
Function .onInit
  SetShellVarContext current
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\PetDock"
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Pet Dock requires 64-bit Windows."
    Abort
  ${EndIf}
  Call VerifyInstallTarget
  Call ConfirmPetDockClosed
FunctionEnd

Function VerifyInstallTarget
  IfFileExists "$INSTDIR\*.*" 0 target_ok
  IfFileExists "$INSTDIR\PetDock.exe" 0 target_not_pet_dock
  IfFileExists "$INSTDIR\app\bootstrap.py" target_ok target_not_pet_dock

target_not_pet_dock:
  MessageBox MB_ICONSTOP "The required Pet Dock installation folder already contains files that are not a recognized Pet Dock installation:$\r$\n$\r$\n$INSTDIR$\r$\n$\r$\nTo avoid overwriting unrelated files, move those files and run the installer again."
  Abort

target_ok:
FunctionEnd

Function ConfirmPetDockClosed
  ; No process is killed. This explicit confirmation also covers an older
  ; installed version whose window title differs from this release.
  MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "Close Pet Dock before continuing. The installer will not close applications for you.$\r$\n$\r$\nAfter Pet Dock is closed, click OK. Click Cancel to stop." IDOK confirm_ok IDCANCEL confirm_cancel
confirm_ok:
  Return
confirm_cancel:
  Abort
FunctionEnd

Section "Pet Dock (required)" SEC_APP
  SetShellVarContext current

  ; The portable root's data/ directory is never copied. Explicit app and
  ; runtime trees preserve nested resources such as cv2/data.
  SetOutPath "$INSTDIR\app"
  File /r "${STAGE}/app/*.*"
  SetOutPath "$INSTDIR\runtime"
  File /r "${STAGE}/runtime/*.*"

  ; Root files are listed explicitly so only owned installation content is
  ; deployed. The installed-only marker is generated below, never copied.
  SetOutPath "$INSTDIR"
  File "${STAGE}/PetDock.exe"
  File "${STAGE}/Check-runtime.cmd"
  File "${STAGE}/Probe-Turmo.cmd"
  File "${STAGE}/Restore-Desktop-Pet.cmd"
  File "${STAGE}/Start.cmd"
  File "${STAGE}/README.md"
  File "${STAGE}/START-HERE.txt"
  File "${STAGE}/LICENSE"
  File "${STAGE}/THIRD_PARTY_NOTICES.md"
  File "${STAGE}/windows-runtime-NOTICES.txt"
  File "${STAGE}/requirements.txt"
  File "${STAGE}/SOURCE.txt"
  File "${STAGE}/FILE-MANIFEST.json"

  ; app_paths.py sees this marker only after installation and uses LocalAppData\PetDock.
  FileOpen $0 "$INSTDIR\installed-mode.txt" w
  FileWrite $0 "Installed per-user by Pet Dock ${VERSION}.$\r$\n"
  FileWrite $0 "Private settings and diagnostics are kept in $LOCALAPPDATA\PetDock.$\r$\n"
  FileClose $0

  WriteUninstaller "$INSTDIR\Uninstall Pet Dock.exe"

  CreateDirectory "$SMPROGRAMS\Pet Dock"
  CreateShortCut "$SMPROGRAMS\Pet Dock\Pet Dock.lnk" "$INSTDIR\PetDock.exe" "" "$INSTDIR\PetDock.exe" 0 SW_SHOWNORMAL
  ; A .cmd target runs through cmd.exe and the script resolves its own installed folder with %~dp0.
  CreateShortCut "$SMPROGRAMS\Pet Dock\Restore Desktop Pet.lnk" "$INSTDIR\Restore-Desktop-Pet.cmd" "" "$INSTDIR\PetDock.exe" 0 SW_SHOWNORMAL
  CreateShortCut "$SMPROGRAMS\Pet Dock\Uninstall Pet Dock.lnk" "$INSTDIR\Uninstall Pet Dock.exe" "" "$INSTDIR\Uninstall Pet Dock.exe" 0 SW_SHOWNORMAL
  CreateShortCut "$DESKTOP\Pet Dock.lnk" "$INSTDIR\PetDock.exe" "" "$INSTDIR\PetDock.exe" 0 SW_SHOWNORMAL

  ; The only registry writes are conventional per-user uninstall metadata.
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "DisplayName" "Pet Dock ${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "DisplayIcon" "$INSTDIR\PetDock.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "UninstallString" "$\"$INSTDIR\Uninstall Pet Dock.exe$\""
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock" "NoRepair" 1

  DetailPrint "Private settings and diagnostics at $LOCALAPPDATA\PetDock are preserved."
SectionEnd

Function un.onInit
  SetShellVarContext current
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\PetDock"
  Call un.ConfirmPetDockClosed
FunctionEnd

Function un.ConfirmPetDockClosed
  MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "Close Pet Dock before continuing. The uninstaller will not close applications for you.$\r$\n$\r$\nAfter Pet Dock is closed, click OK. Click Cancel to stop." IDOK confirm_ok IDCANCEL confirm_cancel
confirm_ok:
  Return
confirm_cancel:
  Abort
FunctionEnd

Section "Uninstall"
  SetShellVarContext current

  Delete "$DESKTOP\Pet Dock.lnk"
  Delete "$SMPROGRAMS\Pet Dock\Pet Dock.lnk"
  Delete "$SMPROGRAMS\Pet Dock\Restore Desktop Pet.lnk"
  Delete "$SMPROGRAMS\Pet Dock\Uninstall Pet Dock.lnk"
  RMDir "$SMPROGRAMS\Pet Dock"

  ; Do not delete $LOCALAPPDATA\PetDock: it contains user settings and diagnostics.
  Delete "$INSTDIR\PetDock.exe"
  Delete "$INSTDIR\Check-runtime.cmd"
  Delete "$INSTDIR\Probe-Turmo.cmd"
  Delete "$INSTDIR\Restore-Desktop-Pet.cmd"
  Delete "$INSTDIR\Start.cmd"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\START-HERE.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.md"
  Delete "$INSTDIR\windows-runtime-NOTICES.txt"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\SOURCE.txt"
  Delete "$INSTDIR\FILE-MANIFEST.json"
  Delete "$INSTDIR\installed-mode.txt"
  Delete "$INSTDIR\Uninstall Pet Dock.exe"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  ; Non-recursive: preserve any unrelated user file that was manually placed here.
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PetDock"
SectionEnd

Function un.onUninstSuccess
  MessageBox MB_ICONINFORMATION "Pet Dock was removed. Your settings and diagnostics were kept at $LOCALAPPDATA\PetDock."
FunctionEnd
