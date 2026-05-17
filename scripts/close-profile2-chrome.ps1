# close-profile2-chrome.ps1
# 關閉 Selenium 相關的 chrome.exe（Profile 2 與 Chrome-Selenium 兩種 profile），
# 並一併清掉 chromedriver.exe 與卡住的 python.exe (selenium_login_test.py)。
# 只關自動化相關 process，不影響其他 Chrome profile 的視窗。
$chromes = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object {
        $_.CommandLine -like "*Profile 2*" -or
        $_.CommandLine -like "*Chrome-Selenium*" -or
        $_.CommandLine -like "*--remote-debugging-port*" -or
        $_.CommandLine -like "*--test-type=webdriver*"
    }
$drivers = Get-CimInstance Win32_Process -Filter "Name='chromedriver.exe'"
$pys = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*selenium_login_test.py*" }

$all = @($chromes) + @($drivers) + @($pys) | Where-Object { $_ -ne $null }
if ($all.Count -gt 0) {
    Write-Host ("Found {0} Selenium-related process(es), closing..." -f $all.Count)
    foreach ($p in $all) {
        Write-Host ("  - PID {0} {1}" -f $p.ProcessId, $p.Name)
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host ("    OK")
        } catch {
            Write-Host ("    FAILED: {0}" -f $_.Exception.Message)
        }
    }
} else {
    Write-Host "No Selenium-related process running."
}

# 清掉 force-kill 後殘留的 profile lock 檔，避免下次 Chrome 啟動時當作 profile in use
$selDir = "$env:LOCALAPPDATA\Chrome-Selenium\User Data"
if (Test-Path $selDir) {
    Remove-Item "$selDir\lockfile" -Force -ErrorAction SilentlyContinue
    Remove-Item "$selDir\Default\LOCK" -Force -ErrorAction SilentlyContinue
    Remove-Item "$selDir\Default\Singleton*" -Force -ErrorAction SilentlyContinue
    Write-Host "Cleared lock files in $selDir"
}
