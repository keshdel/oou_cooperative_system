# CoopMS Mobile App

The mobile app lives at:

```text
mobile/coopms-mobile
```

It is an Expo / React Native app talking to the CoopMS Flask backend. One build
serves every cooperative — there is no per-society build.

## What the app does

- Multi-tenant sign-in: the member enters their society's code, the app resolves
  the backend and brands the sign-in screen
- Login, forgotten password, change password
- Dashboard with savings, share capital, loan balance and profile completion
- Profile view and update
- Savings statement
- **Personal account number** — the member's own NUBAN, with copy-to-clipboard,
  and their choice of what transfers pay for (savings, loan, or Target Advance)
- Loans: list, detail, schedule preview, application with guarantors, withdrawal
- Target Advance (CTAS): subscriptions, status and application — shown only when
  the cooperative has the module switched on
- Notifications, and push-token registration so alerts reach the phone

Sections tied to an optional feature hide themselves when that feature is off,
so a cooperative that does not use Target Advance or account numbers simply does
not see them.

## Run it locally

```powershell
cd C:\OOU_Accounting_System\mobile\coopms-mobile
npm install
npm start
```

Scan the QR code with Expo Go. Type-check with `npm run typecheck`.

Expo Go is for development only. Members get a real build — see below.

## Multi-tenant sign-in

On first launch the member enters their cooperative's **code** (its subdomain,
e.g. `smtcoop`). The app asks HQ to resolve it, falls back to probing
`https://<code>.cooperativems.com/api/mobile/v1/tenant` directly, and stores the
choice on the device. Every request then targets that backend. A **Change
cooperative** link on the sign-in screen switches societies.

A full domain or `https://…` URL is also accepted, for custom-domain tenants.

## Building for members

Builds run on Expo's servers (EAS), so no Android Studio or Mac is needed.

```powershell
npm install --global eas-cli
eas login
```

**Android, for testing on real phones** — produces an APK you can send to a
handful of officers:

```powershell
eas build --platform android --profile preview
```

**Android, for Google Play** — produces the AAB that Play requires:

```powershell
eas build --platform android --profile production
```

**iOS** — needs an Apple Developer account ($99/year):

```powershell
eas build --platform ios --profile production
```

Then submit:

```powershell
eas submit --platform android --latest
```

`eas.json` sets `appVersionSource: remote` with `autoIncrement` on the production
profile, so EAS manages the build number. Bump the user-visible `version` in
`app.json` by hand when the release is worth naming.

## Publishing checklist

- App icon, adaptive icon and splash are in `assets/` and wired in `app.json`
- Package/bundle id: `com.cooperativems.mobile` — changing it after release
  creates a *different* app on the store, so it is fixed now
- A privacy policy URL is required by both stores
- Play data-safety form: the app collects name, email, phone and financial
  account information, all encrypted in transit, and members can request deletion
- `play-store-icon.png` (512×512) in `assets/` is the store listing icon

## Over-the-air updates

`expo-updates` is configured. A build checks for an update on launch, downloads
it in the background, and applies it the next time the app is opened — so a
member is never interrupted mid-task.

**Publishing a JavaScript change** (most fixes, copy, layout, new screens):

```powershell
eas update --branch preview --message "what changed"
```

```powershell
eas update --branch production --message "what changed"
```

No rebuild, no store review, no reinstall. Phones pick it up on their next
launch.

**When a rebuild is unavoidable:**

- a new native module (anything with an `expo install`)
- the icon, splash, permissions or app name
- an SDK upgrade
- a change to `version` in `app.json`

`runtimeVersion` follows `appVersion`, so an update only reaches builds with the
same version number. Bump `version` and you have deliberately cut off older
builds from updates — which is right, because they no longer have the native
code to run them. Leave it alone for JavaScript-only releases.

Each build profile subscribes to a channel of the same name (`preview`,
`production`), set in `eas.json`. `eas update --branch <name>` publishes to it.

The bottom of the Profile screen shows the running version and the first eight
characters of the update id, so a member reporting a problem can tell you exactly
what they are on.

## Distributing to members

The download page is `deploy/vps/landing/app.html`, served at
**https://cooperativems.com/app**. It links to `/download/coopms.apk`.

The APK is not in git — it is build output. After a release build, copy it up:

```powershell
scp coopms.apk root@206.81.30.5:~/oou_cooperative_system/deploy/vps/landing/download/
```

Then it is live. Members who already have the app do **not** need to download it
again unless the release was a native change; everything else reaches them over
the air.

Once the app is on Google Play, replace the download button on `app.html` with
the Play badge and keep the APK as a fallback for members who cannot use Play.

## Not built yet

1. Document upload for loan requirements
2. Guarantor approval screen (a member accepting a request to guarantee)
3. App lock / biometric unlock
4. iOS build (needs an Apple Developer account)
