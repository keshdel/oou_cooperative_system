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

Once `expo-updates` is added, JavaScript-only changes can be pushed without a new
store review. Native changes — a new native module, an icon, permissions — still
need a rebuild. This is not configured yet.

## Not built yet

1. Document upload for loan requirements
2. Guarantor approval screen (a member accepting a request to guarantee)
3. App lock / biometric unlock
4. Over-the-air updates (`expo-updates`)
