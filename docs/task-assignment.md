# Task Assignment — deciding what each officer can do

## The problem

What an officer could do was hard-coded. Every screen carried a fixed list of
roles (`@role_required('admin', 'treasurer')`), so the only way to change who
did what was to edit and redeploy the code. Two consequences showed up in
practice:

* The Treasurer had the money screens but no way into a member's record — the
  members list was Secretary-only, so there was no link to follow.
* A cooperative that shares duties differently (an Exco member who records
  savings, a Secretary who must not touch the ledger) had no way to say so.

## What exists now

Access is expressed as **duties** (permissions), assigned at two levels:

1. **By office** — what a Treasurer, General Secretary or Exco member can do by
   default. Editable at **Settings → Users → Open Task Assignment**, or directly
   at `/task-assignment`.
2. **By officer** — one named person allowed or denied a single duty regardless
   of their office. Each duty is *Follow office*, *Always allow* or *Never
   allow*. Reachable from the shield icon beside a staff account, or from the
   officer list at the bottom of the Task Assignment screen.

Resolution, first match wins:

```
super admin ..................... every duty
President / Administrator ....... every duty        (never editable — see below)
this officer's override ......... allow / deny
their office's setting .......... allow / deny
otherwise ....................... blocked
```

Changes apply on the officer's very next click — no sign-out needed. Menus,
buttons and the screens themselves all follow the same duties, so an officer is
never shown a link that will bounce them.

### The President is always full-access

The `admin` role holds every duty and cannot be edited. Somebody must always be
able to restore access after a mistake, and `system.permissions` — the duty that
governs this screen — can never be given to another office. To limit an account
that currently holds the President role, change its role first under
**Settings → Users**.

## Defaults

The built-in defaults reproduce exactly the access the code used to hard-code,
with one deliberate change: **the Treasurer and Exco can now open the members
list**, not only a member's detail page (they could already open a profile — they
just had no way to reach one). Everything else is unchanged on upgrade.

| Duty | President | Treasurer | General Secretary | Exco |
|---|:--:|:--:|:--:|:--:|
| View members and their profiles | ✔ | ✔ | ✔ | ✔ |
| Add, edit and remove members | ✔ | | ✔ | |
| Review savings-change requests | ✔ | ✔ | ✔ | |
| Generate member ID cards | ✔ | | ✔ | |
| View the savings book | ✔ | ✔ | ✔ | ✔ |
| Record savings and payouts | ✔ | ✔ | | |
| View loan requests and the loan book | ✔ | ✔ | ✔ | ✔ |
| Raise a loan application for a member | ✔ | ✔ | ✔ | ✔ |
| Act on loan approvals and due diligence | ✔ | ✔ | ✔ | |
| Record loan repayments | ✔ | ✔ | | |
| View and record investments | ✔ | ✔ | | |
| View reports | ✔ | ✔ | ✔ | ✔ |
| View the cashbook | ✔ | ✔ | | |
| View the ledgers | ✔ | ✔ | | |
| Post and reverse journal entries | ✔ | ✔ | | |
| Chart of accounts and dividend declaration | ✔ | | | |
| Record expenses and other revenue | ✔ | ✔ | | |
| Manage honorarium payments | ✔ | | | |
| Manage the platform subscription | ✔ | ✔ | | |
| Manage events and minutes | ✔ | | ✔ | |
| Send communications to members | ✔ | | ✔ | |
| Work the leads inbox | ✔ | | ✔ | |
| Read member feedback | ✔ | | | |
| Import/export member data | ✔ | | ✔ | |
| Import/export financial data | ✔ | ✔ | | |
| Opening balances and database tools | ✔ | | | |
| Change system settings | ✔ | | | |
| Manage staff accounts | ✔ | | | |
| Assign what each officer can do | ✔ | — | — | — |

**Restore defaults** on the Task Assignment screen puts every office back to
this table. It does not clear officer-specific overrides; use **Follow the
office** on an officer's own page for that.

## What task assignment does *not* change

The **loan approval chain stays fixed by the bye-laws**: guarantors → Secretary
→ Treasurer → President. The `loans.approve` duty controls whether an officer
can reach the approval screen at all; which stage they may actually sign is
still decided by `loan_workflow.can_act()`. Granting `loans.approve` to an Exco
member does not let them sign the Treasurer's stage.

Separation of duties also still applies: no officer can approve or run due
diligence on their own loan, whatever they are assigned.

## Auditing

Every change is written to the audit log:

| Action | Written when |
|---|---|
| `UPDATE_ROLE_PERMISSIONS` | The office matrix is saved (lists what changed) |
| `RESET_ROLE_PERMISSIONS` | Offices restored to the defaults |
| `UPDATE_USER_PERMISSIONS` | One officer's duties saved |
| `RESET_USER_PERMISSIONS` | An officer's overrides cleared |

## For developers

The catalogue lives in `permissions.py`. Each entry names the duty, its default
offices, and the Flask endpoints it covers:

```python
{
    'key': 'savings.manage',
    'label': 'Record savings and payouts',
    'group': 'Savings',
    'description': 'Post contributions, upload salary batches and record payouts.',
    'default_roles': ('admin', 'treasurer'),
    'endpoints': ('savings.add_saving', 'savings.record_payout',
                  'savings.salary_upload', 'savings.download_salary_template'),
}
```

`role_required(...)` looks the current endpoint up in that catalogue and checks
the duty when one is mapped, falling back to its literal role list otherwise —
so a view added without being catalogued keeps the old rules instead of failing
open. **When you add a guarded view, add its endpoint to a permission**; the
test `test_every_role_guarded_view_is_catalogued` fails if you forget, and
`test_every_catalogued_endpoint_exists_in_the_app` catches typos.

In templates, gate UI on the duty rather than the role:

```jinja
{% if can('savings.manage') %}<a href="...">Record savings</a>{% endif %}
```

In Python, `permissions.user_can('savings.manage')` answers the same question.
Results are cached per request.

Storage: `role_permissions(role, permission, allowed)` and
`user_permissions(user_id, permission, allowed)`. Both are empty on a fresh
database — absent rows mean "use the built-in default" — so nothing needs
seeding and an upgrade changes no one's access.
