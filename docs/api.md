# CoopMS API Notes

## Mobile App V1

All mobile app endpoints use JSON and Bearer token authentication, except tenant
discovery, tenant identity, login, and password reset request.

Base URL is tenant-specific:

- OOU tenant: `https://ooucoop.cooperativems.com`
- SMT tenant: `https://smtcoop.cooperativems.com`
- HQ tenant: `https://hq.cooperativems.com`

### Authentication

`GET /api/mobile/v1/tenants/resolve?code=ooucoop`

Central tenant lookup, intended to be called on the HQ backend. It returns the
tenant API base URL and display name. The app then stores that base URL and uses
the tenant backend for all member operations.

`GET /api/mobile/v1/tenant`

Public tenant identity endpoint on each cooperative backend. Used as a direct
fallback and to verify tenant branding.

`POST /api/mobile/login`

Request:

```json
{
  "username": "member@example.com",
  "password": "MemberPass1!"
}
```

Response includes a short-lived JWT token. Mobile clients should store it in secure storage and send:

```http
Authorization: Bearer <token>
```

Expired mobile JWTs return `401` with `code: "token_expired"` so clients can
clear local session state and return to sign-in.

`POST /api/mobile/v1/auth/forgot-password`

Requests a secure reset link to the user's registered email. The response is
generic to prevent account enumeration.

```json
{
  "identifier": "member@example.com"
}
```

`POST /api/mobile/v1/auth/change-password`

Requires Bearer token authentication and enforces the admin-configured password
policy.

```json
{
  "current_password": "OldPassword1!",
  "new_password": "NewPassword1!",
  "confirm_password": "NewPassword1!"
}
```

### Member App Endpoints

- `GET /api/mobile/v1/me`
- `GET /api/mobile/v1/dashboard`
- `GET /api/mobile/v1/profile`
- `PATCH /api/mobile/v1/profile`
- `GET /api/mobile/v1/savings`
- `GET /api/mobile/v1/loans`
- `POST /api/mobile/v1/loans/schedule-preview`
- `POST /api/mobile/v1/loans/apply`
- `GET /api/mobile/v1/loans/<loan_id>`
- `POST /api/mobile/v1/loans/<loan_id>/withdraw`
- `GET /api/mobile/v1/notifications`
- `POST /api/mobile/v1/notifications/<notification_id>/read`
- `POST /api/mobile/v1/notifications/mark-all-read`
- `POST /api/mobile/v1/devices`

### Device Registration

`POST /api/mobile/v1/devices`

Stores the device push token for future push notifications.

```json
{
  "platform": "android",
  "push_token": "ExpoPushToken[...]",
  "device_name": "Adeo Android"
}
```

### Loan Withdrawal

`POST /api/mobile/v1/loans/<loan_id>/withdraw`

Only works when the loan is still `pending`.

```json
{
  "reason": "I want to revise the loan amount"
}
```

The server keeps the loan record as `withdrawn`, records the approval-trail entry, notifies staff, and does not post any ledger entry.
