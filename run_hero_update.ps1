# 히어로 마스터 앱 일일 자동 갱신
# 데이터브릭스 잡이 구글시트("히어로 PLM 마일스톤 (자동)")를 채운 뒤,
# 이 스크립트가 그 시트를 읽어 app.html 재생성 + GitHub push → Vercel 자동 재배포.
# Windows 작업 스케줄러가 매일 1회 (데이터브릭스 잡 이후 시각) 실행.
Set-Location -Path "C:\Users\MUSINSA\hero_bot"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"`n===== $ts 일일 갱신 시작 =====" | Out-File -FilePath ".\_hero_update.log" -Append -Encoding utf8
python _gen_26fw_heroes.py --sheet --push *>> ".\_hero_update.log"
"===== 종료 (exit $LASTEXITCODE) =====" | Out-File -FilePath ".\_hero_update.log" -Append -Encoding utf8
