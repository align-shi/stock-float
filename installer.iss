; 行情浮窗安装包。用 Inno Setup 编译：ISCC.exe installer.iss

[Setup]
AppId={{A7B3E1C4-6D28-4F5A-9C01-2E8B7D4A6F10}
AppName=行情浮窗
AppVersion=1.1
AppPublisher=行情浮窗
DefaultDirName={localappdata}\StockWidget
DefaultGroupName=行情浮窗
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=行情浮窗安装包
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\StockWidget.exe
SetupIconFile=stock-widget.ico
CloseApplications=yes

[Files]
Source: "dist\StockWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\行情浮窗"; Filename: "{app}\StockWidget.exe"
Name: "{autodesktop}\行情浮窗"; Filename: "{app}\StockWidget.exe"

[Run]
Filename: "{app}\StockWidget.exe"; Description: "启动行情浮窗"; Flags: nowait postinstall skipifsilent
