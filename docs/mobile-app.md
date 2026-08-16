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

## Run Locally

```powershell
cd C:\OOU_Accounting_System\mobile\coopms-mobile
npm install
npm start
```

Then scan the Expo QR code with Expo Go.

## Tenant API Target

The mobile app reads its API base URL from:

```text
mobile/coopms-mobile/app.json
```

Default:

```json
"apiBaseUrl": "https://ooucoop.cooperativems.com"
```

Change this per tenant build if needed.

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
