; ============================================================================
;  ComfyUI 模型下载器 (AICG-DownLoader) —— Windows 安装器脚本
;
;  用 Inno Setup 6 编译（https://jrsoftware.org/isinfo.php）。
;  本脚本不内置版本号，版本由命令行注入：
;
;      iscc /DAppVersion=0.1.0 packaging\windows\installer.iss
;
;  release.yml 里 Windows job 在 cargo build 之后调用上面的命令产出
;  dist\comfy-downloader-<version>-windows-setup.exe。
;
;  设计取舍：这是一款便携 GUI 工具，默认装到 {localappdata}（每用户安装，
;  无需管理员 / UAC 提权），避免普通用户被 Program Files 写权限卡住。
;  如需改为全机安装到 Program Files，把 DefaultDirName 改成
;  {autopf}\AICG-DownLoader 并把 PrivilegesRequired 设为 admin。
; ============================================================================

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "ComfyUI 模型下载器"
#define AppExeName "comfy-downloader.exe"
#define AppPublisher "Winery (WangZhenYu)"
#define AppURL "https://github.com/zhwangsir/AICG-DownLoader"

[Setup]
AppId={{A1C6D0AD-1E5E-4F3B-9C2A-7E3B0F5D2C11}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
AppCopyright=Copyright © 2026 WangZhenYu (Winery)

; 便携工具：每用户安装到 LocalAppData，免管理员。
DefaultDirName={localappdata}\AICG-DownLoader
DefaultGroupName=ComfyUI 模型下载器
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 产物落在仓库根的 dist\，命名与 release.yml 约定一致。
OutputDir=dist
OutputBaseFilename=comfy-downloader-{#AppVersion}-windows-setup
SetupIconFile=
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; 优先简体中文向导；ChineseSimplified.isl 在标准 Inno Setup 6 发行包的
; Languages\ 子目录里自带（GitHub Runner 经 choco 装的 innosetup 同样包含）。
; 万一某环境缺该 isl，退回内置 Default.isl 以保证仍能编译。
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 主程序 + 随附文档/模板。源路径相对仓库根（iscc 的工作目录）。
Source: "target\release\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";                    DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "config.json.example";          DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";                      DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 开始菜单
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
; 桌面（受 desktopicon 任务控制）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 安装结束可选直接运行。
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
