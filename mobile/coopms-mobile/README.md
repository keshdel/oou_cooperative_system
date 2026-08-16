# CoopMS Mobile

Expo/React Native member app for CoopMS.

## First Run

1. Install dependencies:

   ```powershell
   cd C:\OOU_Accounting_System\mobile\coopms-mobile
   npm install
   ```

2. Start Expo:

   ```powershell
   npm start
   ```

3. Open with Expo Go or build a development APK.

## API Target

The default API base URL is configured in `app.json`:

```json
"apiBaseUrl": "https://ooucoop.cooperativems.com"
```

For another tenant, change that value to the tenant domain, for example:

```json
"apiBaseUrl": "https://smtcoop.cooperativems.com"
```

## Implemented V1 Screens

- Login
- Dashboard
- Profile completion and profile update
- Savings statement
- Loan list, loan detail, and pending application withdrawal
- Notifications with mark-all-read
- Push device token registration foundation
