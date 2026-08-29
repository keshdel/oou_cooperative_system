# App store submission — CoopMS mobile

What each store asks for, and the answer for this app. Everything below is
grounded in what the code actually does; if the app changes what it collects,
update this file **and** `deploy/vps/landing/privacy.html` together.

> **Before submitting:** have a Nigerian data-protection lawyer review the
> privacy policy, and fill in every `[PLACEHOLDER]` in it. A wrong Data Safety
> declaration is treated by Google as a policy violation, not a mistake.

## The facts these answers rest on

Verified in the code, 2026-08-29:

- No analytics, crash-reporting, advertising or tracking SDK is bundled. The
  full dependency list is `expo`, `expo-clipboard`, `expo-constants`,
  `expo-font`, `expo-notifications`, `expo-secure-store`, `@expo/vector-icons`,
  `react`, `react-native`.
- No location, camera, contacts, microphone or storage permission is requested.
- Card numbers never reach the app or our servers — Paystack handles them.
- On-device storage is the auth token and the chosen cooperative's code, both in
  `expo-secure-store` (Keychain / Keystore). Sign-out clears both.
- Device registration sends only a push token and the platform string.
- All traffic is HTTPS.
- BVN, NIN and bank account details are encrypted at rest (`crypto.py`).
- Passwords are stored only as a one-way hash.

---

## Google Play — Data Safety form

### Data collection and security (the opening questions)

| Question | Answer |
| --- | --- |
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all user data collected by your app encrypted in transit? | **Yes** |
| Do you provide a way for users to request that their data is deleted? | **Yes** — `privacy@cooperativems.com`, documented in the privacy policy |

### Data types — declare these as collected

For every row: **Collected — Yes**, **Shared — No**, **Processed ephemerally — No**,
**Required (not optional)** unless noted, **Purposes: App functionality** and
**Account management**.

| Category | Data type | Notes |
| --- | --- | --- |
| Personal info | Name | Member's name on their record |
| Personal info | Email address | Sign-in identity |
| Personal info | User IDs | Member number, username |
| Personal info | Address | Postal address on the membership record |
| Personal info | Phone number | Contact and SMS alerts |
| Personal info | Other info | Date of birth, occupation, next of kin, emergency contact |
| Financial info | User payment info | Bank account number for payouts; **no card numbers** |
| Financial info | Other financial info | Savings, share capital, loan and contribution balances |
| App activity | Other actions | In-app actions written to the audit log |
| Device or other IDs | Device or other IDs | Push notification token |

**Photos** — declare only if your cooperatives use member ID photos. The mobile
app itself does not currently upload a photo; it is set from the web portal. If
in doubt, declare it.

### Data types — explicitly NOT collected

Answer **No** to all of these:

- Location (approximate or precise)
- Contacts
- Calendar
- SMS or call logs
- Health and fitness
- Messages (emails, texts, other in-app messages)
- Audio, music, voice recordings
- Files and docs
- Web browsing history
- App info and performance (no crash logs or diagnostics are sent)
- Purchase history

### Data sharing

Declare **not shared** for every type. Google defines "shared" as transfer to a
*third party*, and it explicitly excludes transfers to service providers acting
on your behalf. Our payment, SMS, email and push providers are all processors
under contract, so they do not count as sharing.

> If a reviewer queries this, the answer is: the cooperative is the data
> controller, we are its processor, and our sub-processors act only on our
> instructions. This is stated in sections 1 and 5 of the privacy policy.

### Data deletion

Play requires an account-deletion route reachable **without installing the app**.

- Deletion request URL: `https://cooperativems.com/privacy.html#delete`
- The page must explain both full deletion and what is retained. Ours does —
  financial records are kept for the statutory audit period, and it says so.

### Other Play console answers

| Field | Answer |
| --- | --- |
| App category | Finance |
| Content rating | Everyone (complete the IARC questionnaire — no violence, no user-generated content, no gambling) |
| Target audience | 18 and over |
| Contains ads | **No** |
| In-app purchases | **No** |
| Government app | No |
| Financial features | Select **"Personal loans"** only if the app lets a member *apply* for one — it does. Be ready to show your cooperatives' licences if asked. |
| Privacy policy URL | `https://cooperativems.com/privacy.html` |

> **Watch this one.** Play's financial-services policy asks for proof that
> lenders are licensed. CoopMS is software for registered cooperative societies,
> not a lender — say so plainly in the declaration and attach a client
> cooperative's registration certificate if a reviewer asks.

---

## Apple App Store — Privacy Nutrition Labels

Apple groups the same facts differently.

### Data linked to the user

| Category | Types | Purpose |
| --- | --- | --- |
| Contact Info | Name, Email Address, Phone Number, Physical Address | App Functionality |
| Financial Info | Payment Info, Other Financial Info | App Functionality |
| Identifiers | User ID, Device ID | App Functionality |
| Usage Data | Product Interaction | App Functionality |

### Data not collected

Location, Contacts, Health & Fitness, Sensitive Info, Browsing History, Search
History, Diagnostics, Purchases, Audio, Photos or Videos (unless you enable
member photos in the app), User Content.

### Tracking

**No.** The app does not track users across apps or websites owned by other
companies, so no App Tracking Transparency prompt is needed.

### Other Apple answers

| Field | Answer |
| --- | --- |
| Encryption (`ITSAppUsesNonExemptEncryption`) | Already set to `false` in `app.json` — the app uses only standard HTTPS, which is exempt |
| Age rating | 17+ if you keep the loan-application flow; 4+ otherwise. Choose 17+ to be safe. |
| Sign-in required | Yes — provide a demo account for review (see below) |

---

## What reviewers will need from you

**A working test account.** Both stores reject apps they cannot sign into. Create
a real member on a demo tenant and give the reviewer:

- Cooperative code (the tenant subdomain)
- Username and password
- A note: *"Enter the cooperative code first, then sign in. This app serves many
  separate cooperative societies; the code selects which one."*

Without that note, a reviewer faced with a code prompt will assume the app is
broken and reject it.

**Screenshots.** Both stores want them, and both dislike placeholder data. Use
the demo tenant, not a real cooperative's figures.

---

## Checklist

- [ ] Privacy policy reviewed by a lawyer, placeholders filled
- [ ] `privacy.html` deployed and reachable at `https://cooperativems.com/privacy.html`
- [ ] `privacy@cooperativems.com` mailbox exists and is monitored
- [ ] Demo tenant with a test member created
- [ ] Play developer account registered **as an organisation** (skips the closed-testing requirement that applies to personal accounts)
- [ ] Data Safety form completed as above
- [ ] Screenshots taken from the demo tenant
- [ ] `eas build --platform android --profile production` run and the AAB uploaded
