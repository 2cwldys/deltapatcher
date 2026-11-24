; Uninstalling DeltaPatcher also removes its start-with-the-computer entry (the setting start_with_system),
; so Windows does not try to start a program that is gone. A copy elsewhere with the setting on writes its
; own entry again the next time it starts.
!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "DeltaPatcher"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "DeltaPatcher"
!macroend
