Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.bun\bin\bun.exe"" """ & WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.claude\plugins\marketplaces\thedotmack\scripts\worker-service.cjs"" run", 0, False
