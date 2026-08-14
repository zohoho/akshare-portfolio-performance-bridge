#define MyAppName "AkShare–Portfolio Performance 桥接器"
#define MyAppVersion "2.0.3"
#define MyAppPublisher "AkShare PP Bridge"
#define MyAppExeName "AkSharePPBridge.exe"

[Setup]
AppId={{70CE1A59-2016-43CB-882D-8060290836E2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion=2.0.3.0
AppPublisher={#MyAppPublisher}
LicenseFile={#SourcePath}\LICENSE
DefaultDirName={localappdata}\Programs\AkSharePPBridge
DefaultGroupName=AkShare PP Bridge
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
OutputDir={#SourcePath}\dist
OutputBaseFilename=AkSharePPBridge-Setup-2.0.3
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
SetupLogging=yes

[Files]
Source: "{#SourcePath}\build\package\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AkShare PP Bridge"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\AkShare PP Bridge"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop-service"; Flags: runhidden waituntilterminated; StatusMsg: "正在接管现有报价服务…"
Filename: "{app}\{#MyAppExeName}"; Parameters: "--enable-startup"; Flags: runhidden waituntilterminated; StatusMsg: "正在设置登录自动启动…"
Filename: "{app}\{#MyAppExeName}"; Parameters: "--start-service"; Flags: runhidden waituntilterminated; StatusMsg: "正在启动报价服务…"
Filename: "{app}\{#MyAppExeName}"; Description: "打开 AkShare PP Bridge"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--disable-startup"; Flags: runhidden waituntilterminated; RunOnceId: "DisableStartup"
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop-service"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM AkSharePPBridge.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
