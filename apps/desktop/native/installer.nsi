Unicode true
Name "SpectralIndex Desktop"
OutFile "../../../dist/desktop/SpectralIndex-Desktop-Setup-0.7.0.exe"
InstallDir "$LOCALAPPDATA\Programs\SpectralIndex Desktop Native"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Instalar"
  SetOutPath "$INSTDIR"
  File "build\SpectralIndexDesktop.exe"
  File "build\Microsoft.Web.WebView2.Core.dll"
  File "build\Microsoft.Web.WebView2.WinForms.dll"
  File "build\WebView2Loader.dll"
  File "build\LICENSE.txt"
  File "build\NOTICE.txt"
  File "..\offline-images.tar.gz"
  SetOutPath "$INSTDIR\web"
  File /r "..\..\web\dist\*"
  SetOutPath "$INSTDIR\stack"
  File "..\..\..\docker-compose.yml"
  File "..\docker-compose.desktop.yml"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\SpectralIndex Desktop"
  CreateShortcut "$SMPROGRAMS\SpectralIndex Desktop\SpectralIndex Desktop (leve).lnk" "$INSTDIR\SpectralIndexDesktop.exe"
  CreateShortcut "$SMPROGRAMS\SpectralIndex Desktop\Desinstalar edição leve.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\SpectralIndex Desktop (leve).lnk" "$INSTDIR\SpectralIndexDesktop.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpectralIndexDesktopNative" "DisplayName" "SpectralIndex Desktop (leve)"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpectralIndexDesktopNative" "DisplayVersion" "0.7.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpectralIndexDesktopNative" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\SpectralIndex Desktop (leve).lnk"
  Delete "$SMPROGRAMS\SpectralIndex Desktop\SpectralIndex Desktop (leve).lnk"
  Delete "$SMPROGRAMS\SpectralIndex Desktop\Desinstalar edição leve.lnk"
  RMDir "$SMPROGRAMS\SpectralIndex Desktop"
  Delete "$INSTDIR\SpectralIndexDesktop.exe"
  Delete "$INSTDIR\Microsoft.Web.WebView2.Core.dll"
  Delete "$INSTDIR\Microsoft.Web.WebView2.WinForms.dll"
  Delete "$INSTDIR\WebView2Loader.dll"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\NOTICE.txt"
  Delete "$INSTDIR\offline-images.tar.gz"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\web"
  RMDir /r "$INSTDIR\stack"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpectralIndexDesktopNative"
SectionEnd
