# CoopMS Mobile App Build Guide

The mobile app lives at:

```text
mobile/coopms-mobile
```

It is an Expo/React Native app connected to the existing CoopMS Flask backend.

## Current Scope

Implemented in the mobile app shell:

- Secure login using `/api/mobile/login`
- Dashboard
- Member profile completion
- Profile update
- Savings statement
- Loan list
- Loan detail
- Loan schedule preview
- Loan application submission
- Pending loan withdrawal
- Notifications
- Device push-token registration foundation
- Target Advance (CTAS) — view subscriptions/status and apply, shown only when the
  optional CTAS module is enabled for the cooperative

## Run Locally

```powershell
cd C:\OOU_Accounting_System\mobile\coopms-mobile
npm install
npm start
```

Then scan the Expo QR code with Expo Go.

## Multi-Tenant Login

The app is multi-tenant — one build serves every society. On first launch the
member enters their cooperative's **code** (its subdomain, e.g. `smtcoop`). The
app resolves it to `https://<code>.cooperativems.com`, confirms it via the
public `GET /api/mobile/v1/tenant` endpoint (which returns the coop's name and
logo to brand the sign-in screen), and stores the choice on the device. Every
request then targets that backend. A **Change cooperative** link on the sign-in
screen lets the member switch societies.

A full domain or `https://…` URL is also accepted (for custom-domain tenants).
No per-tenant build is required.

## Android Test Build

After Expo dependencies are installed:

```powershell
cd C:\OOU_Accounting_System\mobile\coopms-mobile
npx expo run:android
```

For cloud/internal distribution later, add EAS:

```powershell
npx eas build:configure
npx eas build --platform android --profile preview
```

## Next Technical Steps

1. Add password reset screen.
2. Add document upload for loan requirements.
3. Add guarantor approval screen.
4. Add push notification sender on the Flask side.
5. Add app lock/biometric unlock.
6. Prepare Android preview build.
