Put the release APK here, named exactly:

    coopms.apk

app.html links to /download/coopms.apk. The file is deliberately not in git — an APK
is ~60 MB of build output and does not belong in source control. Copy it up from
your PC after each release build:

    scp coopms.apk root@206.81.30.5:~/oou_cooperative_system/deploy/vps/landing/download/

Most releases will NOT need this. JavaScript changes reach phones over the air
via `eas update`. A new APK is only needed when something native changes — a new
native module, the icon, permissions, or the app version.
