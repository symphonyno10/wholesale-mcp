; wholesale-mcp Windows Installer
; Inno Setup 6 스크립트
; 빌드: ISCC.exe wholesale-mcp-setup.iss

[Setup]
AppName=wholesale-mcp
AppVersion=1.3.0
AppPublisher=wholesale-mcp
AppPublisherURL=https://github.com/symphonyno10/wholesale-mcp
DefaultDirName={localappdata}\wholesale-mcp
DefaultGroupName=wholesale-mcp
OutputDir=output
OutputBaseFilename=wholesale-mcp-setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
; SetupIconFile=resources\icon.ico
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
; Python embedded + wholesale-mcp + 모든 의존성
Source: "dist\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Run]
; 설치 후: Chromium 다운로드 + Claude Desktop 등록
Filename: "{app}\python\python.exe"; \
  Parameters: """{app}\post-install.py"""; \
  WorkingDir: "{app}"; \
  StatusMsg: "브라우저 설치 및 Claude Desktop 연동 중 (1~2분 소요)..."; \
  Flags: runhidden waituntilterminated

[UninstallRun]
; 삭제 시: Claude Desktop config에서 제거
Filename: "{app}\python\python.exe"; \
  Parameters: "-c ""import json,os;p=os.path.join(os.environ.get('APPDATA',''),'Claude','claude_desktop_config.json');c=json.load(open(p)) if os.path.exists(p) else {};c.get('mcpServers',{}).pop('wholesale-tools',None);open(p,'w').write(json.dumps(c,indent=2))"""; \
  Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\browsers"
Type: filesandordirs; Name: "{app}\credentials.json"

[Messages]
WelcomeLabel2=wholesale-mcp를 설치합니다.%n%nAI(Claude Desktop)에서 도매 사이트 약품 검색, 주문, 매출원장 조회를 자동으로 할 수 있습니다.%n%n설치 후 Claude Desktop을 재시작하면 바로 사용 가능합니다.
FinishedLabel=wholesale-mcp 설치가 완료되었습니다.%n%nClaude Desktop을 재시작한 후,%n"타이레놀 검색해줘"라고 말해보세요.
