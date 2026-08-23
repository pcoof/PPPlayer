; TSPlayer Inno Setup 安装包脚本
; 版本由 CI 通过 /dMyAppVersion= 传入
#define MyAppName "TSPlayer"

[Setup]
AppId=tsplayer-pywebview
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=pcoof
AppPublisherURL=https://github.com/pcoof/tsplayer-pywebview
AppSupportURL=https://github.com/pcoof/tsplayer-pywebview
AppUpdatesURL=https://github.com/pcoof/tsplayer-pywebview/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#SourcePath}\archive
OutputBaseFilename=tsplayer-win-setup
SetupIconFile={#SourcePath}\logo.ico
UninstallDisplayIcon={app}\tsplayer-win.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 普通用户即可安装到用户目录，避免提权弹窗
PrivilegesRequired=lowest
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#SourcePath}\dist\tsplayer-win.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\tsplayer-win.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\tsplayer-win.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\tsplayer-win.exe"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
